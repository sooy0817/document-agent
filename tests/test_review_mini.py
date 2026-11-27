from doc_understanding.runtime import model_client
import json
import pytest
from tests.review_fixtures import fake_transport
from tests.test_review_queue import queued
from tests.test_review_decision_loop import enable, opinion
from tests.review_fixtures import saved as saved

MINI='gpt-4.1-mini-2025-04-14'

def test_mini_request_cost_and_cache_are_model_specific(tmp_path):
    sent=[];run=tmp_path/'run';run.mkdir()
    calls=model_client.ModelCalls(tmp_path,key='test-key',transport=fake_transport(sent),model=MINI)
    result=calls.call('analyst',{'test':True},[],run)
    assert sent[0]['model']==MINI and result['model']==MINI
    assert result['estimated_cost_usd']==pytest.approx((100*.4+20*1.6)/1e6)
    assert calls.call('analyst',{'test':True},[],run)==result and len(sent)==1
    other=model_client.ModelCalls(tmp_path,key='test-key',transport=fake_transport(sent))
    with pytest.raises(ValueError,match='PINNED_REQUEST_CHANGED'):
        other.call('analyst',{'test':True},[],run)
    assert len(sent)==1

def test_new_child_pins_mini_without_replaying_or_relabeling_old(queued):
    q,*_,item=queued;enable(q)
    config=q.data/'review-agent-config.json'
    cfg=json.loads(config.read_text());cfg.update(activated_at='0000',roles_activated_at='0000')
    config.write_text(json.dumps(cfg))
    old=q.enqueue(item['id']);assert q.detail(old)['model']==model_client.MODEL
    q.agent_checker=lambda *args:dict(verdict='insufficient_evidence',validation_errors=[])
    q.run_one();before=q.detail(old)['result']
    cfg['model']=MINI;config.write_text(json.dumps(cfg))
    q.reconcile()
    assert len(q.overview()['items'])==1
    child=q.decide(old,opinion())
    assert q.detail(child)['model']==MINI
    assert q.detail(old)['model']==model_client.MODEL and q.detail(old)['result']==before
    cfg['model']=model_client.MODEL;config.write_text(json.dumps(cfg))
    assert q.detail(child)['model']==MINI
