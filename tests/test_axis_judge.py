import json
import pytest
from tests.test_axis_generation import merged
from doc_understanding.agents.axis_review.axis_judge import AxisJudgeAdapter, JudgeResult, judge_request, validate_judgement, build_judge_graph, DIMENSIONS, score_specs


def result(score=2, verdict='correct'):
    return JudgeResult.model_validate(dict(verdict=verdict, summary='원본 근거에 따른 판정',
        grades=[dict(dimension=d, score=score, evidence_cell_ids=['v1','label','h1'],
                     reason='해당 위치의 원문 제목') for d in DIMENSIONS]))


def test_request_is_blind_and_source_bound():
    r,g=merged(); aliases,data=judge_request(r,g,g.links[0].cell_id)
    assert set(data)=={'source','candidate'}
    assert 'reason' not in data['candidate']
    assert not any(k in json.dumps(data) for k in ['expected','confidence','corrected_structure'])
    assert data['candidate']['row_path'] and len(data['source']['cells'])==len(r.cells)
    assert all(c['span_refs'] for c in data['source']['cells'])
    with pytest.raises(ValueError,match='TARGET_NOT_UNIQUE'):
        judge_request(r,g,'missing')


def test_judge_contract_checks_ids_dimensions_and_verdict_without_certifying():
    r,g=merged(); good=result(); a=validate_judgement(r,good)
    assert a['valid'] and not a['semantic_verified']
    bad=good.model_copy(update={'verdict':'incorrect'})
    assert 'VERDICT_SCORE_CONFLICT' in validate_judgement(r,bad)['errors']
    bad=good.model_copy(update={'grades':good.grades[:-1]})
    assert 'RUBRIC_DIMENSIONS_MISSING_OR_DUPLICATE' in validate_judgement(r,bad)['errors']
    bad=good.model_copy(update={'grades':[x.model_copy(update={'evidence_cell_ids':['invented']}) for x in good.grades]})
    assert 'UNKNOWN_EVIDENCE_ID' in validate_judgement(r,bad)['errors']


def test_unknown_is_not_zero_or_correct():
    r,_=merged(); unknown=result(None,'unjudgeable')
    assert validate_judgement(r,unknown)['valid']
    assert not any(s['data_type']=='NUMERIC' for s in score_specs(unknown))
    assert next(s for s in score_specs(unknown) if s['name']=='axis_judge.verdict')['value']=='unjudgeable'


def test_adapter_is_opt_in_budgeted_and_binds_evidence(monkeypatch):
    r,g=merged(); aliases,_=judge_request(r,g,'v1'); reverse={v:k for k,v in aliases.items()}
    raw=result().model_dump();
    for grade in raw['grades']: grade['evidence_cell_ids']=[reverse[i] for i in grade['evidence_cell_ids']]
    seen=[]
    def transport(payload,key,timeout):
        seen.append(payload)
        assert key=='test-key-not-a-real-openai-credential'
        return dict(status='completed',usage=dict(input_tokens=100,output_tokens=50),
            output=[dict(type='message',content=[dict(type='output_text',text=json.dumps(raw))])])
    with pytest.raises(ValueError,match='EXTERNAL_CALL_NOT_AUTHORIZED'):
        AxisJudgeAdapter(transport=transport).judge(r,g,'v1')
    adapter=AxisJudgeAdapter(allow_external=True,max_calls=1,transport=transport)
    judgement,record=adapter.judge(r,g,'v1',b'fake-image')
    assert judgement==result() and record['status']=='completed'
    assert record['estimated_cost_usd']==pytest.approx(.0006)
    assert 'data:image' not in json.dumps(record)
    assert seen[0]['store'] is False
    with pytest.raises(ValueError,match='CALL_BUDGET_EXCEEDED'):
        adapter.judge(r,g,'v1')
    assert len(seen)==1


def test_graph_runs_once_retains_source_and_does_not_change_candidate():
    r,g=merged(); before=g.model_dump_json(); calls=[]
    def resolver():
        calls.append(1); return result(),{'status':'mock','usage':{},'estimated_cost_usd':0}
    state=build_judge_graph(r,g,'v1',resolver).invoke({})
    assert len(calls)==1 and state['judge_validation']['valid']
    assert g.model_dump_json()==before and not state['semantic_verified']
    assert state['facts_published']==0


def test_transport_failure_remains_unknown_not_false_pass():
    r,g=merged()
    def fail(*args): raise TimeoutError('do not log secrets')
    adapter=AxisJudgeAdapter(allow_external=True,transport=fail)
    judgement,record=adapter.judge(r,g,'v1')
    assert judgement is None and record['usage'] is None and record['estimated_cost_usd'] is None
    assert record['error_code']=='CALL_OR_SCHEMA_FAILED' and 'secrets' not in json.dumps(record)
    state=build_judge_graph(r,g,'v1',lambda:(judgement,record)).invoke({})
    assert not state['judge_validation']['valid'] and state['judgement'] is None
