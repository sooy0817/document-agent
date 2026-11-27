import copy
import hashlib
import json

import pytest

from doc_understanding.tools.semantic_candidates import SemanticCandidate
from doc_understanding.validation.semantic_gate import decide
from doc_understanding.agents.workflows.table_review import graph, service
from tests import review_fixtures

saved = review_fixtures.saved
source = review_fixtures.source


def candidate(**updates):
    return SemanticCandidate(cell_id='C', row_ids=('R',), column_ids=('H',),
                             basis='geometry', role='value', role_uncertain=False).model_copy(update=updates)


@pytest.mark.parametrize('change,expected', [
    ({}, 'rule'), ({'row_ids': ()}, 'llm'), ({'role_uncertain': True}, 'llm'),
    ({'reasons': ('COMPETING_PATHS',)}, 'llm'),
    ({'reasons': ('PHYSICAL_CONTEXT_UNRESOLVED',)}, 'llm'),
    ({'row_ids': ('C',)}, 'llm'), ({'row_ids': ('missing',)}, 'blocked'),
    ({'reasons': ('DEFERRED_FRAGMENT',)}, 'blocked'),
])
def test_gate_never_equates_path_presence_with_sufficient_evidence(change, expected):
    assert decide(candidate(**change), {'C': '17', 'R': 'Group', 'H': 'Reading'}).route == expected


def test_explicit_disagreement_forces_review_without_becoming_gold():
    route = decide(candidate(), {'C': '17', 'R': 'Group', 'H': 'Reading'}, human_conflict=True)
    assert route.route == 'llm' and 'HUMAN_CLAIM_CONFLICT' in route.reasons
    assert not candidate().semantic_verified


def replace_snapshot(data, record, doc):
    path = data / record['snapshot']['job_id'] / 'document.json'
    path.write_text(json.dumps(doc), encoding='utf8')
    record['snapshot']['canonical_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()


def test_rule_only_does_not_access_credentials_or_model_even_if_disabled(source, monkeypatch):
    data, record = source
    path = data / record['snapshot']['job_id'] / 'document.json'
    doc = json.loads(path.read_text(encoding='utf8'))
    for cid, text in [('R', 'Group'), ('H', 'Reading')]:
        doc['physical_tables'][0]['cells'].append({**doc['physical_tables'][0]['cells'][0],
                                                 'cell_id': cid, 'original_text': text})
    replace_snapshot(data, record, doc)
    # This injection tests orchestration only, not the rule algorithm's accuracy.
    monkeypatch.setattr(graph.semantic_candidates, 'generate', lambda *a: [candidate()])
    def forbidden():
        raise AssertionError('No credential lookup on a rule-only route')
    monkeypatch.setattr(service.credentials, 'model_key', forbidden)
    result = service.execute(record, data, lambda s: None, 'a' * 32)
    assert result['api_calls'] == 0 and result['call_records'] == [] and result['model'] is None
    assert result['routing_summary']['rule_retained'] == 1
    assert result['review_complete'] and not result['semantic_verified']


class Responses:
    """Injected model responses test dispatch/coverage, never real-model validation."""
    def __init__(self, fail=False):
        self.seen = []
        self.fail = fail

    def call(self, name, packet, images, run_dir):
        self.seen.append((name, copy.deepcopy(packet), str(run_dir)))
        if self.fail:
            return dict(status='failed', output=None, api_calls=1, estimated_cost_usd=None,
                        error_code='DNS_RESOLUTION_FAILED')
        ids = packet['target_ids']
        texts = {e['id']: e['text'] for e in packet['evidence']}
        if name == 'classify_roles':
            out = dict(summary='ROLE_ONLY', targets=[dict(cell_id=c, role='description', reason='ROLE_ONLY',
                citations=[dict(source_id=c, quote=texts[c])]) for c in ids], missing_evidence=[])
        elif name == 'infer_relations':
            axis = dict(status='unknown', header_ids=[], reason='Uncertain', citations=[])
            out = dict(summary='Uncertain', targets=[dict(cell_id=c, target_quote=texts[c],
                row_axis=axis, column_axis=axis, context_ids=[]) for c in ids], missing_evidence=[])
        else:
            out = dict(summary='Uncertain', decisions=[dict(cell_id=c, axis=a, verdict='insufficient_evidence',
                reason='Uncertain', citations=[]) for c in ids for a in ('row', 'column')],
                coverage=[dict(**c, requirement='insufficient_evidence', reason='Uncertain',
                    citations=[dict(source_id=c['header_id'], quote=texts[c['header_id']])])
                    for c in packet['geometric_candidates']], missing_evidence=[])
        return dict(status='completed', output=out, api_calls=1, estimated_cost_usd=0)


def test_known_role_skips_classifier_but_still_reviews_missing_paths(source, tmp_path, monkeypatch):
    data, record = source
    monkeypatch.setattr(graph.semantic_candidates, 'generate', lambda *a: [
        candidate(row_ids=(), column_ids=(), reasons=('PATH_MISSING',))])
    calls = Responses()
    result = graph.run_review(record, data, tmp_path / 'run', calls=calls)
    assert [n for n, _, _ in calls.seen] == ['infer_relations', 'judge_relations']
    assert result['routing_summary']['llm_completed'] == 1
    assert result['interpretation']['targets'][0]['role'] == 'value'


@pytest.mark.parametrize('fail', [False, True])
def test_more_than_twelve_targets_have_explicit_outcomes_and_no_silent_loss(source, tmp_path, fail):
    data, record = source
    path = data / record['snapshot']['job_id'] / 'document.json'
    doc = json.loads(path.read_text(encoding='utf8'))
    original = doc['physical_tables'][0]['cells'][0]
    doc['physical_tables'][0]['cells'] = [{**original, 'cell_id': f'C{i}'} for i in range(25)]
    record['snapshot']['selection'].update(scope='table', cell_id=None)
    replace_snapshot(data, record, doc)
    calls = Responses(fail=fail)
    result = graph.run_review(record, data, tmp_path / 'run', calls=calls)
    assert {o['cell_id'] for o in result['outcomes']} == {f'C{i}' for i in range(25)}
    assert all(len(p['target_ids']) <= 12 for _, p, _ in calls.seen)
    if fail:
        assert len(calls.seen) == 1 and result['routing_summary']['unresolved'] == 25
        assert 'DNS_RESOLUTION_FAILED' in result['result']['errors']
    else:
        assert len(calls.seen) == 9 and result['routing_summary']['llm_completed'] == 25
        assert len({folder for _, _, folder in calls.seen}) == 3
        assert all('ROLE_ONLY' not in json.dumps(p) for name, p, _ in calls.seen if name != 'classify_roles')
    assert result['semantic_verified'] is False
