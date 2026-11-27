import pytest

pytest.importorskip('langgraph')
from doc_understanding.agents.axis_review.axis_graph import build_axis_graph
from doc_understanding.agents.axis_review.axis_resolver import rule_proposal
from doc_understanding.agents.axis_review.axis_validation import audit_region
from tests.test_axis_resolver import matrix
from tests.test_axis_validation import fragments, propose


def test_complete_rules_skip_model_and_match_old_validator():
    r = matrix()
    def forbidden(_):
        raise AssertionError('unnecessary model call')
    state = build_axis_graph(r, resolver=forbidden).invoke({})
    assert state['model_invocations'] == 0
    assert state['validation'] == audit_region(r, rule_proposal(r)).model_dump(mode='json')
    assert state['route_reason'] == 'NO_UNHANDLED_GEOMETRY_OPTIONS'
    assert not state['semantic_verified'] and state['facts_published'] == 0


def test_only_ambiguous_options_call_model_once_and_preserve_source():
    r = fragments(); before = r.model_dump_json(); calls = []
    def model(source):
        calls.append(source.id)
        return propose(source), {'status': 'mock', 'usage': None}
    state = build_axis_graph(r, resolver=model).invoke({})
    assert calls == [r.id] and state['model_invocations'] == 1
    assert len(state['validation']['checked_links']) == 3
    assert all(c['status'] == 'supported_candidate' for c in state['validation']['checked_links'])
    assert r.model_dump_json() == before
    assert [s['node'] for s in state['steps']] == [
        'inspect_source', 'judge_rules', 'assess_gap', 'judge_llm',
        'validate_paths', 'materialize_candidate']


def test_continuation_model_claims_are_rejected():
    r = fragments()
    r = r.model_copy(update={'cells': tuple(c.model_copy(update={
        'text': 'A long body paragraph that explains conditions, not a heading.'})
        if c.id == 'h1' else c for c in r.cells)})
    state = build_axis_graph(r, resolver=lambda r: (propose(r), {'status': 'mock'})).invoke({})
    assert len(state['validation']['checked_links']) == 3
    assert all('HEADER_EVIDENCE_MISSING' in c['errors'] for c in state['validation']['checked_links'])


def test_model_disabled_and_merged_candidate_gap_are_separate():
    assert build_axis_graph(fragments()).invoke({})['route_reason'] == 'MODEL_DISABLED'
    r = matrix(); r = r.model_copy(update={'cells': tuple(c.model_copy(update={'cs': 2})
        if c.id == 'b' else c for c in r.cells)})
    state = build_axis_graph(r, resolver=lambda _: pytest.fail('no options')).invoke({})
    assert state['route_reason'] == 'NO_GEOMETRY_OPTIONS'
    assert state['model_invocations'] == 0
    assert len(state['validation']['cells']) == len(r.cells)


def test_provider_failure_keeps_rules_and_masks_message():
    def failure(_):
        raise RuntimeError('secret credential must not appear')
    state = build_axis_graph(fragments(), resolver=failure).invoke({})
    assert state['errors'] == ['MODEL_FAILED:RuntimeError']
    assert 'secret credential' not in str(state)
    assert state['model_invocations'] == 1
    assert state['proposal'] == state['rule_proposal']


def test_invalid_source_references_cannot_be_materialized():
    r = fragments(); p = propose(r)
    p = p.model_copy(update={'links': [p.links[0].model_copy(update={'row_path': ['fake']})]})
    state = build_axis_graph(r, resolver=lambda _: (p, {'status': 'mock'})).invoke({})
    assert state['validation']['checked_links'][0]['status'] == 'unresolved'
    assert 'UNKNOWN_CELL_ID' in state['validation']['checked_links'][0]['errors']


def test_model_addition_cannot_downgrade_supported_rules():
    from tests.test_axis_resolver import cell, region
    from doc_understanding.agents.axis_review.axis_resolver import geometry_options, AxisProposal
    # Numeric matrix plus an independent textual fragment; no bank-specific strings.
    base = matrix()
    extra = [cell('x0', '종류', 5, 0, 'extra'), cell('x1', '설명', 5, 1, 'extra'),
             cell('x2', '항목A', 6, 0, 'extra'), cell('x3', '설명내용', 6, 1, 'extra')]
    r = region(list(base.cells) + extra)
    option = next(p for p in geometry_options(r) if p.cell_id == 'x3')
    bad = option.model_copy(update={'column_path': ['e']})  # existing rule value as header
    proposal = AxisProposal(links=[bad], abstain_cell_ids=[])
    state = build_axis_graph(r, resolver=lambda _: (proposal, {'status': 'mock'})).invoke({})
    assert state['errors'] == ['MODEL_BATCH_CONFLICTS_WITH_SUPPORTED_RULES']
    assert state['proposal'] == state['rule_proposal']
    assert len([c for c in state['validation']['checked_links'] if c['status']=='supported_candidate']) == 4
