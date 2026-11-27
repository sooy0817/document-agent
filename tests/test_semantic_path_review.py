from pathlib import Path

import pytest

from doc_understanding.domain.pipeline_contracts import CanonicalDocument
from doc_understanding.services.semantic_path_review import build_packet, render_html

SOURCE = Path('work/document-explorer/85373bab3d984c23beb3abe9663b27d1/document.json')


@pytest.fixture(scope='module')
def frozen():
    if not SOURCE.exists():
        pytest.skip('Local frozen PDF evidence is not bundled')
    return CanonicalDocument.model_validate_json(SOURCE.read_bytes())


def test_existing_paths_can_be_compared_without_reextracting_or_mutating(frozen):
    before = frozen.model_dump_json()
    packet = build_packet(frozen, (31,))
    row = next(r for r in packet.cells if r.cell_id.endswith('cell0013'))
    assert tuple(''.join(x.split()) for x in row.candidate.row_labels) == (
        '소득', '사업소득', '농업소득')
    assert row.candidate.column_labels == ('소득 및 재산 항목 설명',)
    assert row.candidate.row_source_ids and row.candidate.column_source_ids
    assert not row.baseline.row_labels
    assert packet.semantic_verified is False and packet.accuracy_score is None
    assert frozen.model_dump_json() == before
    assert packet.model_dump_json() == build_packet(frozen, (31,)).model_dump_json()


def test_deferred_detached_headers_are_not_processed(frozen):
    packet = build_packet(frozen, (12,))
    assert all(row.candidate is None for row in packet.cells)
    assert not packet.links


def test_already_saved_logical_paths_are_not_reported_as_new_improvement(frozen):
    initial = build_packet(frozen, (31,))
    saved = frozen.model_copy(update={'logical_table_links': initial.links})
    repeated = build_packet(saved, (31,))
    assert repeated.counts['changed_paths'] == 0
    assert repeated.counts['baseline_both_axes'] == repeated.counts['candidate_both_axes']


def test_missing_page_is_not_a_successful_empty_evaluation(frozen):
    with pytest.raises(ValueError, match='PAGE_NOT_FOUND'):
        build_packet(frozen, (999,))


def test_unresolved_source_label_cannot_become_a_resolved_path(frozen):
    tables = []
    for t in frozen.physical_tables:
        if t.page_number == 31:
            t = t.model_copy(update={'cells': tuple(
                c.model_copy(update={'status': 'unresolved'})
                if c.cell_id.endswith('cell0011') else c for c in t.cells)})
        tables.append(t)
    packet = build_packet(frozen.model_copy(update={'physical_tables': tuple(tables)}), (31,))
    row = next(r for r in packet.cells if r.cell_id.endswith('cell0013'))
    # The missing intermediate group must not be hidden by a shorter confident path.
    assert row.candidate.state == 'unresolved'
    assert 'UNRESOLVED_PHYSICAL_CONTEXT' in row.candidate.reasons


def test_report_escapes_source_text_and_never_claims_accuracy(frozen):
    packet = build_packet(frozen, (31,))
    first = packet.cells[0].model_copy(update={'text': '<script>alert(1)</script>'})
    packet = packet.model_copy(update={'cells': (first,) + packet.cells[1:]})
    html = render_html(packet, {})
    assert '<script>alert(1)</script>' not in html
    assert '&lt;script&gt;' in html
    assert '정확도 점수가 아닙니다' in html


def test_shared_rules_route_only_insufficient_real_paths_to_llm(frozen):
    from doc_understanding.tools.semantic_candidates import generate
    from doc_understanding.validation.semantic_gate import decide
    ids = [c.cell_id for t in frozen.physical_tables if t.page_number == 31 for c in t.cells]
    sources = {c.cell_id: c.original_text or '' for t in frozen.physical_tables for c in t.cells}
    candidates = generate(frozen.model_dump(mode='json'), ids)
    routes = [decide(c, sources) for c in candidates]
    assert sum(r.route == 'rule' for r in routes) == 41
    assert sum(r.route == 'llm' for r in routes) == 2
    specific = next(c for c in candidates if c.cell_id.endswith('cell0013'))
    assert decide(specific, sources, human_conflict=True).route == 'llm'
    assert not specific.semantic_verified


def test_deferred_fragment_is_blocked_before_model_dispatch(frozen):
    from doc_understanding.tools.semantic_candidates import generate
    from doc_understanding.validation.semantic_gate import decide
    ids = [c.cell_id for t in frozen.physical_tables if t.page_number == 12 for c in t.cells]
    sources = {c.cell_id: c.original_text or '' for t in frozen.physical_tables for c in t.cells}
    candidates = generate(frozen.model_dump(mode='json'), ids)
    assert candidates
    assert all(decide(c, sources).route == 'blocked' for c in candidates)
