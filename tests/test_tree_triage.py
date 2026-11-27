import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location('triage', Path(__file__).parents[1] / 'scripts/build_tree_triage.py')
triage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(triage)


def test_binding_uncertainty_takes_precedence_over_semantic_diagnosis():
    issue = {'expected': {'row': ['r'], 'column': []}, 'predicted': {'row': ['UNBOUND_PARENT', 'r'], 'column': []}}
    assert triage.classify(issue, True) == 'reference_binding'
    assert triage.classify(issue, False) == 'external_ancestor'


def test_missing_mapping_and_column_conflict_are_distinct():
    issue = {'expected': {'row': ['r'], 'column': []}, 'predicted': {'row': [], 'column': []}}
    assert triage.classify(issue, False) == 'missing_row_mapping'
    issue['predicted']['column'] = ['total']
    assert triage.classify(issue, False) == 'scalar_mapping'


def test_packaged_cases_cover_each_saved_failure_once():
    root = Path(__file__).parents[1]
    diagnosis = triage.read(root / 'data/tree-phase3a-triage/diagnosis.json')
    results = triage.read(root / 'data/tree-phase3a-repair-v3/results.json')
    actual = [m['target'] for c in diagnosis['cases'] for m in c['members']]
    expected = [i['target'] for t in results['after']['tables'] for i in t['issues'] if i['code'] == 'CELL_PATH']
    assert sorted(actual) == sorted(expected)
    assert len(actual) == len(set(actual)) == 60
    assert sum(diagnosis['counts'].values()) == 60
