from types import SimpleNamespace as NS

import pytest

from doc_understanding.domain.models import BBox
from doc_understanding.parser.assembly.nested_content import (
    contains, choose_owner, order_items, ContentItem,
)


def box(x0, y0, x1, y1):
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def test_strict_containment_rejects_same_table_and_partial_overlap():
    parent = box(0, 0, 100, 100)
    assert contains(box(10, 20, 90, 40), parent)
    assert not contains(parent, parent)
    assert not contains(box(90, 20, 130, 40), parent)


def test_innermost_owner_and_competing_owners():
    outer = NS(cell_id='outer', source=NS(bbox=box(0, 0, 100, 100)))
    inner = NS(cell_id='inner', source=NS(bbox=box(10, 10, 90, 90)))
    duplicate = NS(cell_id='duplicate', source=NS(bbox=inner.source.bbox))
    assert choose_owner([outer, inner]) == 'inner'
    assert choose_owner([inner, duplicate]) is None


def item(key, kind, bbox, spans=()):
    return ContentItem(ref_id=key, kind=kind, bbox=bbox, span_ids=spans)


def test_atomic_table_between_prose_and_overlap_abstention():
    before = item('before', 'content', box(0, 0, 100, 9))
    child = item('table', 'table', box(0, 12, 100, 40))
    after = item('after', 'content', box(0, 45, 100, 54))
    ordered, reasons = order_items([after, child, before])
    assert [x.ref_id for x in ordered] == ['before', 'table', 'after']
    assert not reasons
    _, reasons = order_items([child, item('side', 'content', box(110, 15, 150, 25))])
    assert 'READING_ORDER_AMBIGUOUS' in reasons


@pytest.fixture(scope='module')
def baseline():
    from pathlib import Path
    from doc_understanding.domain.pipeline_contracts import CanonicalDocument
    return CanonicalDocument.model_validate_json(Path(
        'reports/lh-full-baseline/run-20261026-150231/canonical-document.json'
    ).read_text(encoding='utf8'))


def test_real_snapshot_is_unchanged_and_links_resolve(baseline):
    from doc_understanding.parser.assembly.nested_content import (
        build_nested_content, validate_links, resolve_cell_content, snapshot_hash,
    )
    original = snapshot_hash(baseline)
    result = build_nested_content(baseline)
    assert snapshot_hash(baseline) == original
    assert validate_links(baseline, result)['references_valid']
    assert result == build_nested_content(baseline)
    assert len(result.pages) == 50
    for page in (38, 39, 47):
        cell = next(c for c in result.cells if c.page_number == page)
        assert cell.child_table_ids
        assert not cell.uncovered_span_ids
        hydrated = resolve_cell_content(baseline, result, cell.parent_cell_id)
        assert hydrated['parent_cell']['cell_id'] == cell.parent_cell_id
        assert any(i['kind'] == 'table' and i['physical'] and i['structured']
                   for i in hydrated['items'])
    changed = baseline.model_copy(update={'source_uri': 'different'})
    with pytest.raises(ValueError, match='SNAPSHOT_MISMATCH'):
        validate_links(changed, result)
    tampered = result.model_copy(update={'cells': ()})
    with pytest.raises(ValueError, match='LINK_RECOMPUTATION_MISMATCH'):
        validate_links(baseline, tampered)


def test_flat_document_skips_additional_assembly(baseline):
    from doc_understanding.parser.assembly.nested_content import build_nested_content
    page = baseline.document_ir.pages[37]
    # Remove the outer detector candidate. The independent inner table remains flat.
    page = page.model_copy(update={'tables': tuple(t for t in page.tables
                                                  if len(t.cells) != 4)})
    doc = baseline.model_copy(update={
        'document_ir': baseline.document_ir.model_copy(update={'pages': (page,)})})
    result = build_nested_content(doc)
    assert result.pages[0].route == 'skip_no_candidate'
    assert not result.cells


def test_missing_physical_table_is_preserved_as_unresolved(baseline):
    from doc_understanding.parser.assembly.nested_content import build_nested_content
    page = baseline.document_ir.pages[37]
    doc = baseline.model_copy(update={
        'physical_tables': (),
        'document_ir': baseline.document_ir.model_copy(update={'pages': (page,)})})
    result = build_nested_content(doc)
    cell = result.cells[0]
    assert cell.status == 'unresolved'
    assert 'PHYSICAL_CHILD_MISSING' in cell.reasons
    assert any(i.kind == 'native_table' for i in cell.items)


def test_duplicate_prose_and_span_gaps_block_resolved_status(baseline):
    from doc_understanding.parser.assembly.nested_content import build_nested_content
    page = baseline.document_ir.pages[37]
    doc = baseline.model_copy(update={
        'document_ir': baseline.document_ir.model_copy(update={'pages': (page,)})})
    original = build_nested_content(doc).cells[0]
    target = next(i.ref_id for i in original.items if i.kind == 'content')
    content = next(c for c in doc.contents if c.content_id == target)
    duplicate = content.model_copy(update={'content_id': target+'/duplicate'})
    repeated = build_nested_content(doc.model_copy(update={'contents': doc.contents+(duplicate,)})).cells[0]
    assert repeated.status == 'unresolved'
    assert 'DUPLICATE_SPAN_OWNERSHIP' in repeated.reasons
    missing = build_nested_content(doc.model_copy(update={
        'contents': tuple(c for c in doc.contents if c.content_id != target)})).cells[0]
    assert missing.status == 'unresolved'
    assert missing.uncovered_span_ids
