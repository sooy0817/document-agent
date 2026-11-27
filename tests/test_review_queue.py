from doc_understanding.review import error_basket
from doc_understanding.runtime import review_queue
from doc_understanding.validation import source_integrity
from doc_understanding.web import app
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from tests.review_fixtures import saved as saved


@pytest.fixture
def queued(saved):
    root, folder, job, request = saved
    data = folder.parent
    folder.rename(data / job['id']); folder = data / job['id']
    root = data / 'error-basket'
    item = error_basket.collect(root, folder, job, request)
    queue = review_queue.ReviewQueue(data)
    return queue, root, folder, job, request, item


def test_revisions_are_idempotent_and_inputs_are_pinned(queued):
    q, root, folder, job, req, item = queued
    old = {p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    source = {p:p.read_bytes() for p in folder.iterdir()}
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids=list(pool.map(lambda _:q.enqueue(item['id']), range(8)))
    assert len(set(ids)) == 1
    error_basket.collect(root,folder,job,req.model_copy(update={'note':'New feedback'}))
    newer=q.enqueue(item['id'])
    assert newer != ids[0]
    assert q.detail(ids[0])['feedback']['note'] == req.note
    assert q.detail(newer)['feedback']['note'] == 'New feedback'
    q.run_one(); q.run_one()
    result=q.detail(ids[0])['result']
    assert result['review_status']=='human_review_pending'
    assert result['api_calls']==0 and not result['semantic_verified']
    assert all(p.read_bytes()==v for p,v in {**old,**source}.items())


def test_restart_recovers_pending_and_pause_is_durable(queued):
    q,*_,item=queued
    rid=q.enqueue(item['id']);q.pause(True)
    assert not q.run_one()
    fresh=review_queue.ReviewQueue(q.data)
    assert fresh.overview()['paused']
    with fresh.db() as c:
        c.execute("UPDATE requests SET status='running',attempt=1 WHERE id=?",(rid,))
    fresh.recover()
    assert fresh.detail(rid)['status']=='queued'
    fresh.pause(False);fresh.run_one()
    assert fresh.detail(rid)['status']=='completed'
    assert fresh.detail(rid)['attempt']==2
    assert any(e['kind']=='interrupted' for e in fresh.detail(rid)['history'])


def test_retry_failure_and_cancel_do_not_erase_history(queued):
    q,*_,item=queued
    def fail(*args): raise RuntimeError('secret text must not leak')
    q.checker=fail;rid=q.enqueue(item['id']);q.run_one()
    assert q.detail(rid)['error']=='RuntimeError'
    q.action(rid,'retry');q.action(rid,'cancel')
    assert not q.run_one()
    q.action(rid,'retry');q.checker=source_integrity.inspect;q.run_one()
    h=q.detail(rid)['history']
    assert {'failed','cancel','completed'} <= {x['kind'] for x in h}
    assert 'secret text' not in json.dumps(h)


def test_cancellation_wins_over_inflight_completion(queued):
    q,*_,item=queued
    started=threading.Event();finish=threading.Event()
    def slow(*args):
        started.set();finish.wait(3)
        return source_integrity.result([])
    q.checker=slow;rid=q.enqueue(item['id'])
    t=threading.Thread(target=q.run_one);t.start()
    assert started.wait(2)
    q.action(rid,'cancel');finish.set();t.join()
    assert q.detail(rid)['status']=='cancelled'
    assert q.detail(rid)['result'] is None


def test_outbox_reconcile_and_inactive_history(queued):
    q,root,*_,item=queued
    q.reconcile();q.reconcile()
    assert len(q.overview()['items'])==1
    error_basket.set_active(root,item['id'],False)
    assert q.enqueue(item['id']) is None
    assert len(q.overview()['items'])==1
    error_basket.set_active(root,item['id'],True)
    q.reconcile()
    assert len(q.overview()['items'])==2


def test_changed_source_is_blocked_without_running_parser(queued):
    q,root,folder,job,req,item=queued
    rid=q.enqueue(item['id'])
    (folder/'document.json').write_text('{}')
    q.run_one();result=q.detail(rid)['result']
    assert result['verdict']=='insufficient_evidence'
    assert result['findings'][0]['code']=='SOURCE_CHANGED'
    assert (folder/'document.json').read_text()=='{}'


def test_no_findings_does_not_approve_semantics():
    r=source_integrity.result([])
    assert r['verdict']=='no_structural_issue_detected'
    assert r['review_status']=='human_review_pending'
    assert not r['training_ready'] and not r['semantic_verified']


def test_dangling_paths_are_errors_but_missing_axes_are_only_review(queued):
    q,root,folder,job,req,item=queued
    rid=q.enqueue(item['id']);q.run_one()
    findings=q.detail(rid)['result']['findings']
    assert any(f['code']=='PATH_REFERENCE_INVALID' and f['severity']=='error' for f in findings)
    assert any(f['code']=='AXIS_PATH_REVIEW' and f['severity']=='review' for f in findings)
    assert all('C' in f['source_ids'] for f in findings if f['code'] in ('PATH_REFERENCE_INVALID','AXIS_PATH_REVIEW'))


def test_reference_save_enqueues_new_revision_without_overwriting_result(queued, monkeypatch):
    q,root,folder,job,req,item=queued
    monkeypatch.setattr(app,'DATA',q.data)
    monkeypatch.setattr(app,'REVIEW_QUEUE',q)
    monkeypatch.setattr(app,'JOBS',{job['id']:job})
    first=q.enqueue(item['id']);q.run_one();before=q.detail(first)
    response=TestClient(app.app).post('/api/error-basket/'+item['id']+'/reference',
        headers={'X-Demo-Token':app.TOKEN},json={'claims':[{'kind':'role','certainty':'uncertain','reason':'원본만으로 역할을 확정하기 어려움'}]})
    assert response.status_code==200
    assert response.json()['review_request_id']!=first
    assert q.detail(first)==before
    assert response.json()['training_ready'] is False


def test_api_collection_delivers_queue_and_polls_result(queued, monkeypatch):
    q,root,folder,job,req,item=queued
    monkeypatch.setattr(app,'DATA',q.data)
    monkeypatch.setattr(app,'REVIEW_QUEUE',q)
    monkeypatch.setattr(app,'JOBS',{job['id']:job})
    # Reuse one event loop; this test explicitly advances the queue itself.
    monkeypatch.setattr(q,'start',lambda:None)
    with TestClient(app.app) as client:
        assert client.post('/api/review-queue/control',json={'paused':True}).status_code==403
        headers={'X-Demo-Token':app.TOKEN}
        response=client.post('/api/jobs/'+job['id']+'/error-basket',headers=headers,json=req.model_dump())
        assert response.status_code==200
        rid=response.json()['review_request_id']
        assert client.get('/api/review-queue').json()['items'][0]['id']==rid
        q.run_one()
        assert client.get('/api/review-queue/'+rid).json()['status']=='completed'
        assert client.post('/api/review-queue/control',headers=headers,json={'paused':True}).json()['paused']


def test_delivery_failure_keeps_basket_and_reconciles(queued,monkeypatch):
    q,root,folder,job,req,item=queued
    monkeypatch.setattr(app,'DATA',q.data)
    monkeypatch.setattr(app,'REVIEW_QUEUE',q)
    monkeypatch.setattr(app,'JOBS',{job['id']:job})
    real=q.enqueue
    def fail(_):raise OSError('unavailable')
    monkeypatch.setattr(q,'enqueue',fail)
    client=TestClient(app.app)
    response=client.post('/api/jobs/'+job['id']+'/error-basket',headers={'X-Demo-Token':app.TOKEN},json=req.model_dump())
    assert response.status_code==200 and response.json()['review_queue_error']
    assert len(error_basket.detail(root,item['id'])['history'])==2
    monkeypatch.setattr(q,'enqueue',real)
    q.reconcile()
    assert len(q.overview()['items'])==1


def test_agent_control_and_new_feedback_delivery(queued,monkeypatch):
    q,root,folder,job,req,item=queued
    monkeypatch.setattr(app,'DATA',q.data)
    monkeypatch.setattr(app,'REVIEW_QUEUE',q)
    monkeypatch.setattr(app,'JOBS',{job['id']:job})
    client=TestClient(app.app); headers={'X-Demo-Token':app.TOKEN}
    assert client.post('/api/review-agents/control',json={'enabled':True}).status_code==403
    assert client.post('/api/review-agents/control',headers=headers,json={'enabled':True}).status_code==200
    q.reconcile(); assert q.overview()['items'][0]['mode']=='local'
    response=client.post('/api/jobs/'+job['id']+'/error-basket',headers=headers,json=req.model_dump())
    rid=response.json()['review_request_id']
    assert q.detail(rid)['mode']=='agents'
    client.post('/api/review-agents/control',headers=headers,json={'enabled':False})
    assert client.post('/api/error-basket/'+item['id']+'/review-request?mode=agents',headers=headers).status_code==409
