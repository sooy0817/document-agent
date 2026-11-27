from doc_understanding.runtime import review_queue
from doc_understanding.web import app
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from tests.review_fixtures import saved as saved
from tests.test_review_queue import queued
from tests.test_review_decision_loop import enable, opinion


def confirmation(ids=None, confirmed=True):
    return dict(confirmed=confirmed,note='선택 범위를 원본과 비교했습니다.',source_ids=ids or [])


def test_confirmation_closes_only_original_scope_without_models_or_source_mutation(queued):
    q,root,folder,*_,item=queued
    rid=q.enqueue(item['id']);q.run_one()
    before=q.detail(rid)['result'];sources={p:p.read_bytes() for p in folder.iterdir() if p.is_file()}
    # No enabled model setting required; confirming a cell leaves whole-region attention.
    q.confirm(rid,confirmation(['C']))
    assert q.overview()['alerts'] and not q.detail(rid)['confirmation']
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _:q.confirm(rid,confirmation()),range(6)))
    state=q.overview()
    assert not state['alerts'] and len(state['items'])==1
    assert q.detail(rid)['result']==before
    assert not q.detail(rid)['confirmation']['model_candidate_applied']
    assert len([e for e in q.detail(rid)['history'] if e['kind']=='human_confirmation'])==2
    assert all(p.read_bytes()==v for p,v in sources.items())
    fresh=review_queue.ReviewQueue(q.data)
    assert fresh.detail(rid)['confirmation']['scope']=='original_request_selection'
    with pytest.raises(HTTPException):q.decide(rid,opinion())
    q.confirm(rid,confirmation(confirmed=False))
    assert q.overview()['alerts'] and not q.detail(rid)['confirmation']
    assert len(q.detail(rid)['confirmations'])==1  # earlier cell confirmation remains


def test_confirm_invalid_or_active_or_old_request_rejected_and_opinion_exposed(queued):
    q,*_,item=queued;enable(q)
    rid=q.enqueue(item['id'],mode='local')
    with pytest.raises(HTTPException):q.confirm(rid,confirmation())
    q.run_one()
    with pytest.raises(HTTPException):q.confirm(rid,confirmation(['foreign']))
    child=q.decide(rid,opinion())
    assert q.detail(child)['submitted_opinion']['note']==opinion()['note']
    with pytest.raises(HTTPException):q.confirm(rid,confirmation())


def test_confirmation_api_auth_and_no_child(queued,monkeypatch):
    q,*_,item=queued;rid=q.enqueue(item['id']);q.run_one()
    monkeypatch.setattr(app,'REVIEW_QUEUE',q)
    monkeypatch.setattr(q,'start',lambda:None)
    with TestClient(app.app) as client:
        url='/api/review-queue/'+rid+'/confirmation'
        assert client.post(url,json=confirmation()).status_code==403
        h={'X-Demo-Token':app.TOKEN}
        assert client.post(url,headers=h,json=confirmation(['foreign'])).status_code==422
        response=client.post(url,headers=h,json=confirmation())
        assert response.status_code==200 and response.json()['confirmation']
        assert len(q.overview()['items'])==1
        assert client.post(url,headers=h,json={**confirmation(),'note':''}).status_code==422
