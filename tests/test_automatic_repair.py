import pytest
from doc_understanding.experiments.parser_repair import automatic, engine
from doc_understanding.evaluation.parser.reference_checks import evaluate


def source_fixture():
    d=dict(document_id='sha256:x',physical_tables=[dict(table_id='child',cells=[dict(cell_id='parent',original_text='설명')])],
           table_structure=[],document_ir=dict(pages=[]))
    n=dict(cells=[dict(parent_cell_id='parent',containment_status='linked',child_table_ids=['child'],
                      coverage_status='complete',reading_order_status='total',items=[dict(ref_id='child')])])
    r=dict(schema_version='human-reference/1.0',status='reference_draft',document_id='sha256:x',
           source_records=[dict(id='parent',text='설명'),dict(id='child',text='표 전체')],
           claims=[dict(kind='nested_table',certainty='proposed',source_id='parent',target_id='child',
                        relation='link',evidence=[dict(source_id='parent',quote='설명')])])
    return d,n,r


@pytest.mark.parametrize('damage',[None,'uncertain','unsupported','invented_quote','missing_id','unlinked','duplicate','order','empty'])
def test_reference_never_silently_drops_unsupported_claim(damage):
    d,n,r=source_fixture();c=r['claims'][0]
    if damage=='uncertain':c['certainty']='uncertain'
    if damage=='unsupported':c['kind']='row_header'
    if damage=='invented_quote':c['evidence'][0]['quote']='없는 원문'
    if damage=='missing_id':c['target_id']='missing'
    if damage=='unlinked':n['cells'][0]['containment_status']='unresolved'
    if damage=='duplicate':n['cells'][0]['items']*=2
    if damage=='order':n['cells'][0]['reading_order_status']='partial'
    if damage=='empty':r['claims']=[]
    result=evaluate(d,n,r)
    assert result['passed'] is (damage is None)
    assert result['human_approved'] is False


@pytest.mark.parametrize('case',['no_reference','failed','already_correct','one_regression','code_changed','passing'])
def test_automatic_promotion_gate(tmp_path,monkeypatch,case):
    data=tmp_path/'data';source=data/'job'/'input.pdf';source.parent.mkdir(parents=True);source.write_bytes(b'fixture')
    out=tmp_path/'attempt';out.mkdir()
    ref=dict(source_pdf_sha256=engine.sha(source),canonical_sha256='snapshot',status='reference_draft')
    record=dict(snapshot=dict(job_id='job',source_pdf_sha256=engine.sha(source),canonical_sha256='snapshot',selection=dict(page=1)),reference=ref)
    if case=='no_reference':record.pop('reference')
    monkeypatch.setattr(engine,'freeze',lambda dest:dest.mkdir())
    def runner(code,dest,command,pdf):
        engine.save(dest/'document.json',dict(physical_tables=[],document_ir=dict(pages=[dict(page_number=1,tables=[])])))
        engine.save(dest/'nested.json',dict(cells=[]));return 0
    def execute(*args,**kwargs):
        engine.save(out/'proposal.json',dict(edits=[dict(path='parser.py')]))
        engine.save(out/'code-manifest.json',{'parser.py':'old'})
        datasets=[dict(dataset=dict(role='development'),comparison=dict(source_checks=dict(baseline=dict(passed=case=='already_correct'),candidate=dict(passed=True))))]
        for i in range(1 if case=='one_regression' else 2):datasets.append(dict(dataset=dict(role='regression',sha256=str(i)),comparison={}))
        return dict(status='validation_failed' if case=='failed' else 'awaiting_source_review',candidate_modified=True,
                    stable_parser_modified=False,stable_code_unchanged=case!='code_changed',acceptance_passed=case not in ('failed','no_reference'),datasets=datasets)
    monkeypatch.setattr(engine,'execute',execute)
    applied=[]
    monkeypatch.setattr('doc_understanding.experiments.parser_repair.registered_plan.apply_verified',lambda *args:applied.append(True))
    result=automatic.execute(record,data,out,lambda *args:None,runner=runner)
    assert not applied
    assert not result['stable_parser_modified']
    if case=='passing':
        assert result['status']=='awaiting_source_review'
        assert result['human_review_required']
    if case=='failed':assert result['status']=='validation_failed'
    if case=='no_reference':assert result['status']=='needs_source_reference'


@pytest.mark.parametrize('after',["import os\ndef f(): return 1", "def f(): return open('secrets').read()", "def f(): return eval('1')"])
def test_generated_parser_cannot_add_host_capabilities(after):
    with pytest.raises(ValueError,match='CAPABILITY'):
        automatic.check_capabilities('def f(): return 1',after)


def test_comment_only_patch_is_not_a_parser_improvement():
    with pytest.raises(ValueError,match='NO_BEHAVIOR_CHANGE'):
        automatic.check_capabilities('def f(): return 1','def f(): return 1 # implement later')
