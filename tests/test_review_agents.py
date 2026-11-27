from tests.review_fixtures import source as source, analyst_outputs as outputs, fake_transport
from doc_understanding.review import error_basket
from doc_understanding.runtime import model_client
from doc_understanding.runtime import review_queue
from doc_understanding.agents.workflows.legacy_review import graph
from doc_understanding.tools import evidence
from doc_understanding.validation import review
import copy
import hashlib
import json
from pathlib import Path

import pytest

from tests.review_fixtures import saved as saved








def test_graph_is_bounded_preserves_inputs_and_replays_without_new_calls(source):
    data,record=source;before={p:p.read_bytes() for p in data.rglob('*.json')}
    calls=[];adapter=model_client.ModelCalls(data,key='test-key',transport=fake_transport(calls))
    stages=[]
    result=graph.run_review(record,data,stages.append,'a'*32,calls=adapter)
    assert stages==['collect_evidence','analyze','judge','validate_review','report_ready']
    assert len(calls)==2 and result['api_calls']==2
    assert result['validation_errors']==[]
    assert not result['semantic_verified'] and not result['training_ready'] and not result['parser_modified']
    again=graph.run_review(record,data,lambda _:None,'a'*32,calls=adapter)
    assert again['analysis']==result['analysis'] and len(calls)==2
    assert all(p.read_bytes()==raw for p,raw in before.items())
    packet=json.loads(calls[1]['input'][1]['content'][0]['text'])
    assert packet['analyst_candidate']==result['analysis']
    assert 'reference' not in packet and 'history' not in packet


def test_judge_disagreement_is_not_majority_approved(source):
    data,record=source;a,j=outputs();j['assessments'][0]['verdict']='disagree'
    result=graph.run_review(record,data,lambda _:None,'b'*32,calls=model_client.ModelCalls(data,key='test',transport=fake_transport([], (a,j))))
    assert result['verdict']=='insufficient_evidence'
    assert result['judge']['assessments'][0]['verdict']=='disagree'


@pytest.mark.parametrize('error',['quote','id','coverage','self'])
def test_output_checks_reject_invalid_evidence(error):
    a,j=outputs();packet={'evidence':[dict(id='C',text='17',table_id='T')]}
    if error=='quote': a['issues'][0]['citations'][0]['quote']='999'
    if error=='id': a['issues'][0]['target_ids']=['invented']
    if error=='coverage': j['assessments']=[]
    if error=='self': a['issues'][0].update(relation='row_header',source_id='C',target_id='C')
    assert review.check_outputs(packet,a,j)


def test_changed_source_blocks_all_model_calls(source):
    data,record=source;(data/record['snapshot']['job_id']/'document.json').write_text('{}')
    calls=[]
    result=graph.run_review(record,data,lambda _:None,'c'*32,calls=model_client.ModelCalls(data,key='test',transport=fake_transport(calls)))
    assert not calls and result['verdict']=='insufficient_evidence'


def test_interrupted_call_never_silently_rebills_and_daily_limit(source):
    data,record=source;packet,images=evidence.collect_evidence(record,data)
    run=data/'interrupted';run.mkdir();(run/'analyst-started.json').write_text('{}')
    calls=[];adapter=model_client.ModelCalls(data,key='test',transport=fake_transport(calls),daily_limit=1)
    assert adapter.call('analyst',packet,images,run)['status']=='interrupted_unknown_usage'
    assert not calls
    other=data/'new';other.mkdir();assert adapter.call('analyst',packet,images,other)['status']=='completed'
    assert adapter.call('judge',packet,images,other)['error']=='DAILY_CALL_BUDGET'
    assert len(calls)==1


def test_failed_api_cost_unknown_and_no_auto_retry(source):
    data,record=source;calls=[]
    def fail(*args):calls.append(1);raise TimeoutError('do not leak key')
    result=graph.run_review(record,data,lambda _:None,'d'*32,calls=model_client.ModelCalls(data,key='test',transport=fail))
    assert len(calls)==1 and result['estimated_cost_usd'] is None
    assert 'do not leak' not in json.dumps(result)
    assert result['verdict']=='insufficient_evidence'


def test_old_records_are_not_automatically_billed_and_mode_is_versioned(source):
    data,record=source
    (data/'review-agent-config.json').write_text(json.dumps(dict(enabled=True,activated_at='9999',daily_limit=20)))
    q=review_queue.ReviewQueue(data,agent_checker=lambda *args:dict(mode='agents'))
    q.reconcile();old=q.overview()['items'][0];assert old['mode']=='local'
    rid=q.enqueue(record['id'],mode='agents');assert rid!=old['id']
    q.run_one();q.run_one();assert q.detail(rid)['result']['mode']=='agents'
    assert len(q.overview()['items'])==2


def test_cancel_after_analysis_does_not_call_judge(source):
    data,record=source;calls=[]
    def progress(stage):
        if stage=='judge': raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError,match='cancelled'):
        graph.run_review(record,data,progress,'e'*32,calls=model_client.ModelCalls(data,key='test',transport=fake_transport(calls)))
    assert len(calls)==1
    assert (data/'review-queue'/'agents'/('e'*32)/'analyst.json').exists()


def test_single_cell_role_does_not_require_a_fake_second_endpoint():
    a,j=outputs();a['issues'][0].update(relation='role',source_id='C',target_id=None)
    assert not review.check_outputs({'evidence':[dict(id='C',text='17',table_id='T')]},a,j)


def test_cycle_check_retains_multiple_children():
    a,j=outputs();base=a['issues'][0]
    a['issues']=[{**base,'issue_id':str(k),'relation':'row_header','source_id':s,'target_id':t} for k,(s,t) in enumerate([('C','D'),('C','E'),('D','C')])]
    j['assessments']=[{**j['assessments'][0],'issue_id':i['issue_id']} for i in a['issues']]
    assert 'RELATION_CYCLE' in review.check_outputs({'evidence':[dict(id=i,text='17',table_id='T') for i in 'CDE']},a,j)
