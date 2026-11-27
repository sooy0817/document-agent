from doc_understanding.experiments.parser_repair import parser_refresh as refresh
from doc_understanding.experiments.parser_repair import engine
from doc_understanding.experiments.parser_repair import queue as parser_experiments_queue
from doc_understanding.runtime import review_queue
import json
import pytest


def test_refresh_preserves_snapshot_and_never_calls_proposer(tmp_path, monkeypatch):
    saved=tmp_path/'job';saved.mkdir()
    (saved/'input.pdf').write_bytes(b'synthetic test pdf')
    doc=dict(document_ir=dict(pages=[dict(page_number=1,blocks=[],tables=[])]),physical_tables=[],table_structure=[])
    for name,value in [('document.json',doc),('nested.json',dict(cells=[])),('result.json',{})]:
        engine.save(saved/name,value)
    original=(saved/'document.json').read_bytes()
    record=dict(snapshot=dict(job_id='job',source_pdf_sha256=engine.sha(saved/'input.pdf'),canonical_sha256=engine.sha(saved/'document.json')))
    monkeypatch.setattr(engine,'freeze',lambda path:{})
    def prohibited(*args):raise AssertionError('No model allowed')
    monkeypatch.setattr(engine,'propose',prohibited)
    def runner(code,out,command,source):
        for name in ('document.json','nested.json','result.json'):
            engine.save(out/name,engine.read(saved/name))
        return 0
    result=refresh.execute(record,tmp_path,tmp_path/'run',lambda *args:None,runner=runner)
    assert result['usage']['api_calls']==0
    assert result['datasets'][0]['comparison']['native_text_preserved']
    assert result['datasets'][0]['comparison']['pages'][0]['after']['both_paths']==0
    assert not result['semantic_accuracy_verified']
    assert original==(saved/'document.json').read_bytes()
    record['snapshot']['canonical_sha256']='changed'
    with pytest.raises(ValueError,match='SAVED_OUTPUT_CHANGED'):
        refresh.execute(record,tmp_path,tmp_path/'other',lambda *args:None,runner=runner)


def test_paid_retry_counts_attempt_not_distinct_request(tmp_path):
    reviews=review_queue.ReviewQueue(tmp_path)
    with reviews.db() as c:
        c.execute("INSERT INTO requests(id,item_id,revision,payload,status,stage,created_at,updated_at) VALUES('r','i','v','{}','completed','done','2020','2020')")
    def fail(*args):raise RuntimeError('failed')
    queue=parser_experiments_queue.RepairQueue(tmp_path,reviews,executor=fail)
    queue.enable(True,'code_patch')
    ident=queue.enqueue('r')
    assert queue.run_one()
    queue.retry(ident)
    assert queue.run_one()
    queue.retry(ident)
    assert not queue.run_one()
    assert len([e for e in queue.overview()['items'][0]['events'] if e['stage']=='paid_started'])==2
    # Local refresh is independent of exhausted paid-model allowance.
    queue.enable(True,'local_refresh')
    assert queue.run_one()
    assert queue.overview()['items'][0]['costs'][-1]['estimated_cost_usd']==0
