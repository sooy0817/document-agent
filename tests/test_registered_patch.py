import pytest
from doc_understanding.experiments.parser_repair import engine
from doc_understanding.experiments.parser_repair.registered_plan import apply_verified


def setup_patch(tmp_path, monkeypatch):
    monkeypatch.setattr(engine,'ROOT',tmp_path/'workspace')
    name=engine.ALLOWED[0];target=engine.ROOT/name
    target.parent.mkdir(parents=True);target.write_text('old')
    folder=tmp_path/'attempt';candidate=folder/'candidate'/name
    candidate.parent.mkdir(parents=True);candidate.write_text('new')
    return folder,target,dict(baseline_hashes={name:engine.sha(target)},proposal=dict(edits=[dict(path=name)]))


def test_passing_checks_cannot_replace_last_human_approval(tmp_path,monkeypatch):
    folder,target,plan=setup_patch(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='ACCEPTANCE_REQUIRED'):
        apply_verified(folder,plan,dict(acceptance_passed=False))
    assert target.read_text()=='old'
    result=dict(acceptance_passed=True)
    with pytest.raises(ValueError,match='HUMAN_APPROVAL_WORKFLOW_NOT_CONNECTED'):
        apply_verified(folder,plan,result)
    assert target.read_text()=='old'
    assert not (folder/'applied.json').exists()


def test_concurrent_code_edit_blocks_application(tmp_path,monkeypatch):
    folder,target,plan=setup_patch(tmp_path,monkeypatch)
    target.write_text('someone else changed this')
    with pytest.raises(ValueError,match='HUMAN_APPROVAL_WORKFLOW_NOT_CONNECTED'):
        apply_verified(folder,plan,dict(acceptance_passed=True))
    assert target.read_text()=='someone else changed this'


def test_same_applied_plan_refreshes_without_rewriting_or_model(tmp_path,monkeypatch):
    from doc_understanding.experiments.parser_repair import registered_plan,parser_refresh
    folder,target,plan=setup_patch(tmp_path,monkeypatch)
    target.write_text('new')
    plan['proposal']['edits'][0]['after']='new'
    plan['reference']={'source_pdf_sha256':'source'}
    monkeypatch.setattr(parser_refresh,'execute',lambda *args:dict(mode='local_refresh'))
    def forbidden(*args,**kwargs):raise AssertionError('No patch/model execution allowed')
    monkeypatch.setattr(engine,'execute',forbidden)
    result=registered_plan.execute(dict(snapshot=dict(source_pdf_sha256='source')),tmp_path,folder,lambda *args:None,plan)
    assert result==dict(mode='local_refresh') and target.read_text()=='new'


def test_legacy_apply_flag_cannot_bypass_last_human_review(tmp_path,monkeypatch):
    from doc_understanding.experiments.parser_repair import registered_plan
    folder,target,plan=setup_patch(tmp_path,monkeypatch)
    plan.update(reference={'source_pdf_sha256':'source'}, tests=[], apply_to_workspace=True)
    plan['proposal']['edits'][0]['after']='new'
    monkeypatch.setattr(engine,'execute',lambda *a,**kw:dict(
        acceptance_passed=True, candidate_modified=True, stable_parser_modified=False))
    def forbidden(*a,**kw):raise AssertionError('Implicit promotion forbidden')
    monkeypatch.setattr(registered_plan,'apply_verified',forbidden)
    result=registered_plan.execute(dict(snapshot=dict(source_pdf_sha256='source')),
                                   tmp_path,folder,lambda *a:None,plan)
    assert result['status']=='awaiting_source_review'
    assert result['human_review_required'] and not result['stable_parser_modified']
    assert target.read_text()=='old'
