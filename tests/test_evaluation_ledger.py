import json
import pytest
from doc_understanding.evaluation.ledger import register, verify, import_reviews, encoded, digest, put


def test_snapshot_is_immutable_and_registration_is_idempotent(tmp_path):
    workspace=tmp_path/'work'
    workspace.mkdir()
    source=workspace/'result.json'
    source.write_text('{"score":1}')
    ledger=tmp_path/'ledger'
    spec={'title':'example','dataset_role':'development','result_kind':'test','metadata':{},'artifacts':['result.json']}
    a=register(ledger,workspace,spec)
    assert register(ledger,workspace,spec).run_id==a.run_id
    source.write_text('{"score":2}')
    b=register(ledger,workspace,spec)
    assert a.run_id!=b.run_id and verify(ledger)==2
    assert (ledger/'objects'/a.artifacts['result.json']).read_text()=='{"score":1}'
    (ledger/'objects'/a.artifacts['result.json']).write_text('tamper')
    with pytest.raises(ValueError):
        verify(ledger)


def test_review_import_rejects_wrong_scope_and_keeps_corrections_separate(tmp_path):
    q=encoded({'cases':[{'case_id':'one'}]})
    h=digest(q)
    put(tmp_path/'queues'/f'{h}.json',q)
    batch={'schema_version':'evaluation-review/1.0','queue_sha256':h,'reviewer':'tester','reviews':[{'case_id':'one','decision':'agree','note':''}]}
    p=tmp_path/'input.json'
    p.write_bytes(encoded(batch))
    first=import_reviews(tmp_path,p)
    assert import_reviews(tmp_path,p)==first
    assert json.loads(first.read_text())['gold_modified'] is False
    batch['reviews'][0]['case_id']='other'
    p.write_bytes(encoded(batch))
    with pytest.raises(ValueError):
        import_reviews(tmp_path,p)
    batch['reviews'][0]={'case_id':'one','decision':'correction','note':''}
    p.write_bytes(encoded(batch))
    with pytest.raises(ValueError):
        import_reviews(tmp_path,p)
    batch['reviews'][0]['note']='Different scope'
    p.write_bytes(encoded(batch))
    assert import_reviews(tmp_path,p)!=first
    assert len(list((tmp_path/'reviews').glob('*.json')))==2


def test_registration_cannot_escape_workspace(tmp_path):
    work=tmp_path/'work'
    work.mkdir()
    (tmp_path/'outside').write_text('private')
    with pytest.raises(ValueError):
        register(tmp_path/'ledger',work,{'title':'x','dataset_role':'x','result_kind':'x','metadata':{},'artifacts':['../outside']})
