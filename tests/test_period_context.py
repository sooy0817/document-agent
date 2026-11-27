import copy
import json

import pytest

from tests.test_axis_resolver import cell, region
from doc_understanding.parser.context.period_context import PeriodTarget, PeriodRelation, PeriodProposal, PeriodAssessment, PeriodInventory, compare_period_context, validate_proposal, validate_inventory, period_request, PeriodContextAdapter, build_period_graph


def fixture():
    r = region([cell('basis', '이용 개시부터 종료까지', 0, 0),
                cell('duration', '두 달 이내', 1, 0), cell('value', '0', 1, 1),
                cell('other-row', '별도 항목', 2, 0), cell('other', '-2', 2, 1)])
    rel = PeriodRelation(relation_type='defines_period_basis', source_cell_ids=['basis'],
        applies_to=[PeriodTarget(row_cell_id='duration', value_cell_id='value')],
        evidence_cell_ids=['basis', 'duration', 'value'], reason='source period scope')
    proposal = PeriodProposal(relations=[rel], unknown_target_ids=[], reason='source')
    inv = PeriodInventory(assessments=[
        PeriodAssessment(target_cell_id='value', status='required', expected_relations=[rel],
                         evidence_cell_ids=['basis', 'duration', 'value'], reason='explicit basis'),
        PeriodAssessment(target_cell_id='other', status='not_needed', expected_relations=[],
                         evidence_cell_ids=['other-row', 'other'], reason='unrelated item')])
    return r, proposal, inv


def test_before_after_and_source_unchanged():
    r, p, inv = fixture()
    frozen = r.model_dump_json()
    before = compare_period_context(r, ['value', 'other'], p.model_copy(update={'relations':[]}), inv)
    after = compare_period_context(r, ['value', 'other'], p, inv)
    assert [x['completeness_score'] for x in before['targets']] == [1, 2]
    assert [x['completeness_score'] for x in after['targets']] == [2, 2]
    assert before['targets'][0]['missing'][0]['source_cell_ids'] == ['basis']
    assert after['targets'][0]['missing'] == []
    assert not after['semantic_verified'] and after['facts_published'] == 0
    assert r.model_dump_json() == frozen


def test_scope_expansion_is_error_even_when_all_required_edges_present():
    r, p, inv = fixture()
    rel = p.relations[0]
    rel = rel.model_copy(update=dict(applies_to=rel.applies_to+[
        PeriodTarget(row_cell_id='other-row', value_cell_id='other')],
        evidence_cell_ids=rel.evidence_cell_ids+['other-row','other']))
    result = compare_period_context(r, ['value', 'other'], p.model_copy(update={'relations':[rel]}), inv)
    assert result['valid']
    assert result['targets'][0]['verdict'] == 'correct'
    assert result['targets'][1]['scope_score'] == 0
    assert result['targets'][1]['verdict'] == 'incorrect'


def test_wrong_row_pair_cannot_pass_by_using_correct_value_id():
    r, p, inv = fixture()
    rel = p.relations[0].model_copy(update=dict(applies_to=[PeriodTarget(row_cell_id='other-row',value_cell_id='value')],
                                               evidence_cell_ids=['basis','other-row','value']))
    result = compare_period_context(r,['value','other'],p.model_copy(update={'relations':[rel]}),inv)
    assert result['targets'][0]['scope_score'] == 0
    assert result['targets'][0]['missing'] and result['targets'][0]['extra']


def test_unknown_is_not_absent_or_perfect_and_invalid_inventory_is_blocked():
    r, p, inv = fixture()
    unknown = inv.assessments[0].model_copy(update=dict(status='unknown',expected_relations=[]))
    inv = inv.model_copy(update={'assessments':[unknown, inv.assessments[1]]})
    result = compare_period_context(r,['value','other'],p,inv)
    assert result['targets'][0]['verdict'] == 'unjudgeable'
    assert result['targets'][0]['missing'] is None
    inv = inv.model_copy(update={'assessments':[unknown]})
    assert not compare_period_context(r,['value','other'],p,inv)['valid']


@pytest.mark.parametrize('change,error', [
    ({'source_cell_ids':['fake']}, 'UNKNOWN_RELATION_CELL'),
    ({'evidence_cell_ids':['basis']}, 'RELATION_EVIDENCE_MISSING'),
    ({'source_cell_ids':['value']}, 'SELF_RELATION'),
    ({'source_cell_ids':['basis','duration']}, 'SELF_RELATION'),
    ({'applies_to':[]}, 'EMPTY_RELATION'),
])
def test_invalid_proposal_not_published(change,error):
    r,p,inv=fixture()
    p=p.model_copy(update={'relations':[p.relations[0].model_copy(update=change)]})
    assert error in validate_proposal(r,['value','other'],p)
    assert not compare_period_context(r,['value','other'],p,inv)['valid']


def test_duplicate_relation_and_wrong_inventory_status_are_blocked():
    r,p,inv=fixture()
    assert 'DUPLICATE_RELATION_EDGE' in validate_proposal(r,['value','other'],p.model_copy(update={'relations':p.relations*2}))
    inv=inv.model_copy(update={'assessments':[inv.assessments[0].model_copy(update={'status':'not_needed'}),inv.assessments[1]]})
    assert 'INVENTORY_STATUS_CONFLICT' in validate_inventory(r,['value','other'],inv)


def test_graph_blind_inventory_same_input_bounded_mock_calls():
    r,p,inv=fixture();seen=[]
    aliases,data=period_request(r,['value','other']);reverse={v:k for k,v in aliases.items()}
    from doc_understanding.parser.context.period_context import _bind
    def transport(payload,key,timeout):
        assert key=='test-key-not-a-real-openai-credential'
        seen.append(copy.deepcopy(payload))
        result=p if len(seen)==1 else inv
        body=_bind(result.model_dump(mode='json'),reverse)
        return dict(status='completed',usage=dict(input_tokens=100,output_tokens=100),
            output=[dict(type='message',content=[dict(type='output_text',text=json.dumps(body))])])
    adapter=PeriodContextAdapter(allow_external=True,max_calls=2,transport=transport)
    result=build_period_graph(r,['value','other'],b'fake image',adapter).invoke({})
    assert result['after']['targets'][0]['verdict']=='correct'
    assert seen[0]['input'][1]==seen[1]['input'][1]
    req=json.loads(seen[1]['input'][1]['content'][0]['text'])
    assert set(req)=={'source','target_value_ids'}
    assert 'reference' not in req and 'candidate' not in req
    assert 'data:image' not in json.dumps(result)
    with pytest.raises(ValueError,match='CALL_BUDGET'):adapter.call(r,['value'],b'', 'propose')


def test_no_network_by_default_and_unavailable_model_fail_closed():
    r,_,_=fixture()
    with pytest.raises(ValueError,match='NOT_AUTHORIZED'):
        PeriodContextAdapter().call(r,['value'],b'', 'propose')
    def fail(*args): raise RuntimeError('mock failure')
    adapter=PeriodContextAdapter(allow_external=True,max_calls=2,transport=fail)
    state=build_period_graph(r,['value'],b'',adapter).invoke({})
    assert not state['after']['valid'] and not state['before']['valid']
    assert state['after']['targets']==[]
