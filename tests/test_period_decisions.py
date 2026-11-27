import copy
import json
import pytest
from tests.test_period_context import fixture
from doc_understanding.parser.context.period_context import period_request, _bind
from doc_understanding.parser.context.period_decisions import PeriodDecisions, PeriodDecision, DefinitionApplication, validate_decisions, compare_decisions, derived_references, DecisionAdapter, build_decision_graph, legacy_decisions


def decisions():
    return PeriodDecisions(decisions=[
        PeriodDecision(target_cell_id='value', status='required', relations=[
            DefinitionApplication(definition_cell_ids=['basis'], duration_label_cell_id='duration')],
            supporting_cell_ids=[], reason='기간 측정 기준이 인쇄됨', missing_evidence=None),
        PeriodDecision(target_cell_id='other', status='not_needed', relations=[],
            supporting_cell_ids=['other-row'], reason='기간과 무관한 별도 항목', missing_evidence=None)])


def test_explicit_decisions_and_derived_refs_preserve_source():
    r, _, _ = fixture(); frozen = r.model_dump_json(); d = decisions()
    assert validate_decisions(r, ['value','other'], d) == []
    assert derived_references(d.decisions[0]) == ['basis','duration','value']
    assert all(t['verdict']=='agreement' for t in compare_decisions(r,['value','other'],d,d)['targets'])
    assert r.model_dump_json() == frozen


@pytest.mark.parametrize('change,error', [
    ({'status':'not_needed'}, 'STATUS_RELATION_CONFLICT'),
    ({'status':'insufficient_evidence','relations':[]}, 'MISSING_EVIDENCE_REASON_REQUIRED'),
    ({'missing_evidence':'前ページ'}, 'UNEXPECTED_MISSING_EVIDENCE'),
    ({'relations':[DefinitionApplication(definition_cell_ids=['duration'],duration_label_cell_id='duration')]}, 'ROLE_CONFLICT'),
    ({'relations':[DefinitionApplication(definition_cell_ids=['fake'],duration_label_cell_id='duration')]}, 'UNKNOWN_CELL'),
])
def test_conflicts_block_comparison(change,error):
    r,_,_=fixture(); d=decisions()
    bad=d.model_copy(update={'decisions':[d.decisions[0].model_copy(update=change),d.decisions[1]]})
    assert error in validate_decisions(r,['value','other'],bad)
    assert not compare_decisions(r,['value','other'],bad,d)['valid']


def test_omitted_target_is_not_implicit_not_needed():
    r,_,_=fixture(); d=decisions()
    assert 'TARGET_COVERAGE' in validate_decisions(r,['value','other'],d.model_copy(update={'decisions':d.decisions[:1]}))


def test_insufficient_is_not_absence_or_semantic_success():
    r,_,_=fixture(); d=decisions()
    unknown=d.decisions[1].model_copy(update={'status':'insufficient_evidence','missing_evidence':'앞쪽 설명이 제공되지 않음'})
    uncertain=d.model_copy(update={'decisions':[d.decisions[0],unknown]})
    result=compare_decisions(r,['value','other'],uncertain,d)['targets'][1]
    assert result['verdict']=='over_abstention' and result['state_match'] is False
    result=compare_decisions(r,['value','other'],uncertain,uncertain)['targets'][1]
    assert result['verdict']=='unjudgeable' and result['completeness_score'] is None


def test_wrong_scope_has_extra_edge_even_with_valid_ids():
    r,_,_=fixture(); d=decisions()
    wrong=d.decisions[0].model_copy(update={'relations':[DefinitionApplication(definition_cell_ids=['basis'],duration_label_cell_id='other-row')]})
    result=compare_decisions(r,['value','other'],d.model_copy(update={'decisions':[wrong,d.decisions[1]]}),d)
    assert result['targets'][0]['scope_score']==0
    assert result['targets'][0]['missing'] and result['targets'][0]['extra']


def test_graph_blind_judge_and_mock_network():
    r,_,_=fixture(); seen=[]; d=decisions()
    aliases,_=period_request(r,['value','other']); reverse={v:k for k,v in aliases.items()}
    def transport(payload,key,timeout):
        assert key=='test-key-not-a-real-openai-credential'
        seen.append(copy.deepcopy(payload))
        return dict(status='completed',usage=dict(input_tokens=100,output_tokens=100),output=[dict(type='message',content=[dict(type='output_text',text=json.dumps(_bind(d.model_dump(mode='json'),reverse)))])])
    adapter=DecisionAdapter(allow_external=True,max_calls=2,transport=transport)
    state=build_decision_graph(r,['value','other'],b'fake',adapter).invoke({})
    assert seen[0]['input'][1]==seen[1]['input'][1]
    assert state['comparison']['valid'] and not state['comparison']['semantic_verified']
    assert state['sidecar']['derived_source_refs'][0]['cell_ids']==['basis','duration','value']
    with pytest.raises(ValueError,match='BUDGET'): adapter.call(r,['value'],b'', 'propose')


def test_legacy_replay_derives_refs_without_reinterpreting_unknown():
    r,p,_=fixture()
    rel=p.relations[0].model_copy(update={'evidence_cell_ids':['basis']})
    old=p.model_copy(update={'relations':[rel],'unknown_target_ids':['other']})
    frozen=old.model_dump_json()
    converted=legacy_decisions(old,['value','other'])
    assert validate_decisions(r,['value','other'],converted)==[]
    assert derived_references(converted.decisions[0])==['basis','duration','value']
    assert converted.decisions[1].status=='insufficient_evidence'
    assert old.model_dump_json()==frozen


def test_legacy_role_conflict_not_repaired_or_accepted():
    r,p,_=fixture()
    rel=p.relations[0].model_copy(update={'source_cell_ids':['basis','duration']})
    converted=legacy_decisions(p.model_copy(update={'relations':[rel]}),['value','other'])
    assert 'ROLE_CONFLICT' in validate_decisions(r,['value','other'],converted)


def test_unavailable_model_not_interpreted_as_no_relation():
    r,_,_=fixture()
    with pytest.raises(ValueError,match='NOT_AUTHORIZED'):
        DecisionAdapter().call(r,['value'],b'', 'propose')
    def fail(*args):raise RuntimeError('mock unavailable')
    result=build_decision_graph(r,['value'],b'',DecisionAdapter(allow_external=True,max_calls=2,transport=fail)).invoke({})
    assert result['sidecar']['status']=='rejected'
    assert not result['comparison']['valid'] and result['comparison']['targets']==[]
