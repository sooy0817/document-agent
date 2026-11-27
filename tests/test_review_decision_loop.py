from doc_understanding.review import error_basket
from doc_understanding.runtime import review_queue
from doc_understanding.agents.workflows.table_review import service
from doc_understanding.validation import source_integrity
from doc_understanding.web import app
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from tests.test_review_queue import queued
from tests.review_fixtures import saved as saved


def enable(q):
    (q.data/'review-agent-config.json').write_text(json.dumps(dict(enabled=True,daily_limit=20,activated_at='9999',roles_activated_at='9999')))


def test_routing_upgrade_does_not_replay_existing_paid_request(queued):
    import hashlib
    q, *_, item = queued
    enable(q)
    rid = q.enqueue(item['id'], mode='agents_roles')
    record = error_basket.detail(q.basket, item['id'])
    old_revision = hashlib.sha256(('review-decision-loop/1' + review_queue.pack(record)).encode()).hexdigest()
    with q.db() as c:
        c.execute('UPDATE requests SET revision=? WHERE id=?', (old_revision, rid))
    assert q.enqueue(item['id'], mode='agents_roles') == rid
    assert q.enqueue(item['id'], mode='agents_roles', execution_key='explicit-new-workflow') != rid


def opinion(choice='disagree'):
    return dict(choice=choice,note='오른쪽 설명은 왼쪽 항목에 속합니다.',source_ids=['C'])


def test_decision_atomic_idempotent_and_immutable_result(queued):
    q,root,folder,*_,item=queued;enable(q)
    rid=q.enqueue(item['id'],mode='local');q.run_one()
    result=q.detail(rid)['result'];snapshot={p:p.read_bytes() for p in folder.iterdir() if p.is_file()}
    with ThreadPoolExecutor(max_workers=4) as pool:
        children=list(pool.map(lambda _:q.decide(rid,opinion()),range(6)))
    assert len(set(children))==1
    child=children[0]
    assert q.detail(child)['parent_id']==rid and q.detail(child)['mode']=='agents_roles'
    assert q.detail(rid)['result']==result
    assert not q.overview()['alerts']  # waiting for child, no repeat alert from parent
    assert all(p.read_bytes()==b for p,b in snapshot.items())
    with pytest.raises(HTTPException) as e:q.decide(rid,opinion('uncertain'))
    assert e.value.status_code==409


def test_decision_reanalysis_receives_claim_and_comparison(queued):
    q,*_,item=queued;enable(q)
    rid=q.enqueue(item['id'],mode='local');q.run_one()
    child=q.decide(rid,opinion())
    def fake(record,data,progress,rid):
        assert record['review_decision']['note']==opinion()['note']
        progress('infer_relations')
        return {**source_integrity.result([]),'mode':'agents_roles','validation_errors':[],
                'interpretation':{'targets':[dict(cell_id='C',row_axis={'header_ids':['L']},column_axis={'header_ids':[]})]}}
    q.agent_checker=fake;q.run_one()
    result=q.detail(child)['result']
    assert result['comparison']['changed_cells']==['C']
    assert not result['comparison']['accuracy_improvement_verified']
    assert not q.overview()['alerts']
    restarted=review_queue.ReviewQueue(q.data)
    assert restarted.detail(child)['result']==result
    assert restarted.detail(rid)['decision']['child_id']==child


def test_alerts_only_latest_actionable_not_every_completed(queued):
    q,root,folder,job,req,item=queued
    rid=q.enqueue(item['id']);q.run_one()
    assert [a['request_id'] for a in q.overview()['alerts']]==[rid]
    error_basket.collect(root,folder,job,req.model_copy(update={'note':'new'}))
    newer=q.enqueue(item['id']);q.checker=lambda *args:source_integrity.result([])
    q.run_one()
    assert not q.overview()['alerts']
    assert len(q.overview()['items'])==2
    with pytest.raises(HTTPException):q.decide(rid,opinion())


def test_future_mode_cutoff_does_not_reprocess_old_basket(queued):
    q,root,folder,job,req,item=queued;enable(q)
    old=q.enqueue(item['id'])
    cfg=dict(enabled=True,daily_limit=20,activated_at='0000',roles_activated_at=review_queue.now())
    (q.data/'review-agent-config.json').write_text(json.dumps(cfg))
    # In deployment the original activation cutoff is preserved; here make it future for old events.
    cfg['activated_at']=cfg['roles_activated_at']
    (q.data/'review-agent-config.json').write_text(json.dumps(cfg))
    q.reconcile();assert len(q.overview()['items'])==1
    error_basket.collect(root,folder,job,req.model_copy(update={'note':'fresh event'}))
    newer=q.enqueue(item['id'])
    assert q.detail(old)['mode']=='local' and q.detail(newer)['mode']=='agents_roles'


def test_decision_api_auth_validation_and_duplicate(queued,monkeypatch):
    q,*_,item=queued;enable(q);rid=q.enqueue(item['id'],mode='local');q.run_one()
    monkeypatch.setattr(app,'REVIEW_QUEUE',q)
    with TestClient(app.app) as client:
        # Prevent the test worker from making calls. All requests remain queued.
        q.pause(True)
        url='/api/review-queue/'+rid+'/decision'
        assert client.post(url,json=opinion()).status_code==403
        h={'X-Demo-Token':app.TOKEN}
        assert client.post(url,headers=h,json={**opinion(),'source_ids':['foreign']}).status_code==422
        first=client.post(url,headers=h,json=opinion())
        assert first.status_code==200
        assert client.post(url,headers=h,json=opinion()).json()['id']==first.json()['id']
        assert client.post(url,headers=h,json={**opinion(),'note':''}).status_code==422


def test_changed_source_stops_before_models(queued,tmp_path):
    q,root,folder,job,req,item=queued
    record=error_basket.detail(root,item['id'])
    (folder/'document.json').write_text('{}')
    class NoCalls:
        def call(self,*args):raise AssertionError('must not call')
    result=service.run(record,q.data,lambda _:None,'f'*32,calls=NoCalls())
    assert 'SOURCE_CHANGED' in result['validation_errors']
    assert result['api_calls']==0 and not result['semantic_verified']
