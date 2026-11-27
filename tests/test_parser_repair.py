from doc_understanding.evaluation.parser.snapshots import delta
from doc_understanding.experiments.parser_repair import engine
from doc_understanding.experiments.parser_repair import queue
from doc_understanding.runtime import review_queue
import json
from pathlib import Path
import pytest


def test_patch_isolated_and_exact(tmp_path):
    base=tmp_path/'base'; name=engine.ALLOWED[0]
    (base/name).parent.mkdir(parents=True);(base/name).write_text('def f():\n    return 1\n')
    proposal={'edits':[dict(path=name,before='return 1',after='return 2',reason='generic correction')]}
    diff=engine.apply_proposal(base,tmp_path/'candidate',proposal)
    assert 'return 1' in (base/name).read_text()
    assert 'return 2' in (tmp_path/'candidate'/name).read_text()
    assert '-    return 1' in diff


@pytest.mark.parametrize('path',['../escape.py','tests/test_physical_grid.py','src/doc_understanding/parser/physical/physical_models.py'])
def test_reject_scope(tmp_path,path):
    with pytest.raises(ValueError,match='PATCH_SCOPE'):
        engine.apply_proposal(tmp_path,tmp_path/'candidate',{'edits':[dict(path=path,before='a',after='b',reason='x')]})
    assert not (tmp_path/'candidate').exists()


def test_ambiguous_replacement_rejected_before_write(tmp_path):
    name=engine.ALLOWED[0];(tmp_path/name).parent.mkdir(parents=True);(tmp_path/name).write_text('a=1\nb=1\n')
    with pytest.raises(ValueError,match='CONTEXT'):
        engine.apply_proposal(tmp_path,tmp_path/'candidate',{'edits':[dict(path=name,before='1',after='2',reason='x')]})
    assert not (tmp_path/'candidate').exists()


def test_delta_not_accuracy():
    d=dict(physical_tables=[dict(cells=[dict(cell_id='c',original_text='0')])],table_structure=[dict(cell_mappings=[])],
           document_ir=dict(pages=[dict(blocks=[dict(spans=[dict(span_id='s',text='0')])])]))
    other=json.loads(json.dumps(d));other['document_ir']['pages'][0]['blocks'][0]['spans'][0]['text']='1'
    result=delta(d,other)
    assert not result['native_text_preserved']
    assert not result['semantic_accuracy_verified']


def test_container_has_no_network_or_host_secrets(tmp_path,monkeypatch):
    calls=[]
    class Result:returncode=0;stdout=b'ok';stderr=b''
    monkeypatch.setattr(engine.subprocess,'run',lambda cmd,**kwargs:(calls.append(cmd) or Result()))
    engine.container(tmp_path,tmp_path/'out',['python','-m','pytest'])
    assert calls[0][calls[0].index('--network')+1]=='none'
    assert '--read-only' in calls[0]
    assert all('docker.sock' not in str(v) for v in calls[0])


def test_queue_deduplicates_and_does_not_replay_history(tmp_path):
    pass
    q=review_queue.ReviewQueue(tmp_path)
    payload=dict(snapshot=dict(job_id='a'*32,document_name='synthetic',selection={'page':1},source_pdf_sha256='x',canonical_sha256='y'),feedback={})
    with q.db() as c:
        c.execute("INSERT INTO requests(id,item_id,revision,payload,status,stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",('r','item','revision',json.dumps(payload),'completed','done','2020','2020'))
    worker=queue.RepairQueue(tmp_path,q,executor=lambda *args:dict(status='awaiting_source_review'))
    worker.enable(True);worker.reconcile()
    assert worker.overview()['items']==[]
    first=worker.enqueue('r');assert worker.enqueue('r')==first
    assert worker.run_one()
    assert worker.overview()['items'][0]['status']=='awaiting_source_review'
    assert not worker.run_one()


def test_failure_retained_without_retry_loop(tmp_path):
    pass
    q=review_queue.ReviewQueue(tmp_path)
    with q.db() as c:c.execute("INSERT INTO requests(id,item_id,revision,payload,status,stage,created_at,updated_at) VALUES('r','i','v','{}','completed','done','2020','2020')")
    def fail(*args):raise RuntimeError('SOURCE_HASH_MISMATCH')
    worker=queue.RepairQueue(tmp_path,q,executor=fail);worker.enable(True);worker.enqueue('r')
    assert worker.run_one()
    assert worker.overview()['items'][0]['status']=='failed'
    assert not worker.run_one()


def test_daily_limit_explains_wait_without_losing_request(tmp_path):
    q=review_queue.ReviewQueue(tmp_path)
    with q.db() as c:c.execute("INSERT INTO requests(id,item_id,revision,payload,status,stage,created_at,updated_at) VALUES('r','i','v','{}','completed','done','2020','2020')")
    worker=queue.RepairQueue(tmp_path,q);worker.enable(True,'automatic');ident=worker.enqueue('r')
    worker.progress(ident,'model_call','one');worker.progress(ident,'model_call','two')
    assert not worker.run_one()
    item=worker.overview()['items'][0]
    assert item['status']=='queued' and item['stage']=='budget_wait'
    assert not worker.run_one()
    assert sum(e['stage']=='budget_wait' for e in worker.overview()['items'][0]['events'])==1
