"""검색 표현의 원문 보존 회귀. 합성 자료이며 실제 검색 정확도 평가는 아니다."""
import copy

import pytest

from doc_understanding.domain.models import BBox, BlockIR, SourceRef, TextSpanIR
from doc_understanding.parser.assembly.document import assemble, build_from_run
from doc_understanding.parser.extraction.html_adapter import prepare_html
from doc_understanding.rag.evidence.pack import evidence_pack
from doc_understanding.rag.indexing.chunking import paragraph_groups, text_slices
from doc_understanding.rag.indexing.entries import make_entries
from tests.support import sample_ir


def body_document(lines):
    ir = sample_ir()
    spans = []
    for index, text in enumerate(lines):
        source = SourceRef(document_id=ir.document_id, page_number=1, block_id='body',
            span_id=f'body-{index}', bbox=BBox(x0=0, x1=90, y0=index * 12, y1=index * 12 + 10))
        spans.append(TextSpanIR(span_id=source.span_id, source=source, text_raw=text,
            line_index=index, font='fixture', font_size=10, flags=0))
    block = BlockIR(block_id='body', kind='text', extraction_order=0,
        source=spans[0].source.model_copy(update={'span_id': None}), spans=tuple(spans))
    page = ir.pages[0].model_copy(update={'blocks': (block,), 'tables': ()})
    return assemble(ir.model_copy(update={'pages': (page,)}), (), ())


@pytest.mark.parametrize('raw', ['', '한글🌱' * 400, '  première ligne.\r\n次の行。 ' * 100,
                                  'x' * 601 + ' ' * 900])
def test_slices_cover_every_character_without_overlap(raw):
    slices = text_slices(raw)
    assert ''.join(raw[a:b] for a, b in slices) == raw
    assert all(0 < b - a <= 600 for a, b in slices)
    assert all(left[1] == right[0] for left, right in zip(slices, slices[1:]))


@pytest.mark.parametrize('limit', [0, -1, True, 1.5])
def test_invalid_limit_is_rejected(limit):
    with pytest.raises(ValueError, match='INVALID_CHUNK_LIMIT'):
        text_slices('원문', limit)


def test_paragraph_search_joins_lines_but_evidence_restores_original_conditions():
    doc = body_document(['Store in a dry room.', 'Except during cleaning; do not seal.'])
    before = doc.model_dump(mode='json')
    entries = make_entries(doc)
    assert len(entries) == 1
    hit = entries[0]
    assert hit['content_ids'] == [c.content_id for c in doc.contents]
    assert hit['primary_text'] == '\n'.join(c.text_raw for c in doc.contents)
    evidence = evidence_pack(doc, [hit, hit], 'How should it be stored?')
    assert [e['value_raw'] for e in evidence] == [c.text_raw for c in doc.contents]
    assert all(e['source_references'] and e['span_refs'] for e in evidence)
    assert doc.model_dump(mode='json') == before
    assert make_entries(doc) == entries


def test_long_body_chunks_map_to_sources_and_restore_full_text():
    doc = body_document(['Réparer 🌱. ' * 90, 'ただし雨天は作業しない。' * 60])
    entries = make_entries(doc)
    contents = {c.content_id: c for c in doc.contents}
    full = '\n'.join(c.text_raw for c in doc.contents)
    assert len(entries) > 1
    assert ''.join(e['primary_text'] for e in entries) == full
    for hit in entries:
        assert hit['primary_text'] == full[hit['char_start']:hit['char_end']]
        for segment in hit['source_segments']:
            raw = contents[segment['content_id']].text_raw
            assert raw[segment['char_start']:segment['char_end']] == hit['primary_text'][
                segment['chunk_start']:segment['chunk_end']]
    evidence = evidence_pack(doc, [entries[0]], 'Réparer')
    assert [e['value_raw'] for e in evidence] == [c.text_raw for c in doc.contents]


@pytest.mark.parametrize('boundary', ['page', 'block', 'section', 'order', 'column', 'gap',
                                     'note', 'duplicate', 'excluded', 'unresolved', 'table'])
def test_paragraph_groups_do_not_cross_uncertain_boundaries(boundary):
    doc = body_document(['First description.', 'Second description.'])
    a, b = doc.contents
    excluded = set()
    if boundary in ('page', 'block', 'column', 'gap'):
        ref = b.source_references[0]
        updates = {'page_number': 2} if boundary == 'page' else {'block_id': 'other'}
        if boundary == 'column':
            updates = {'bbox': BBox(x0=200, x1=290, y0=12, y1=22)}
        if boundary == 'gap':
            updates = {'bbox': BBox(x0=0, x1=90, y0=70, y1=80)}
        b = b.model_copy(update={'source_references': (ref.model_copy(update=updates),)})
    elif boundary == 'section':
        b = b.model_copy(update={'section_id': 'other'})
    elif boundary == 'order':
        b = b.model_copy(update={'reading_order': 4})
    elif boundary == 'note':
        b = b.model_copy(update={'kind': 'note'})
    elif boundary == 'duplicate':
        b = b.model_copy(update={'duplicate_of_table_ids': ('table',)})
    elif boundary == 'excluded':
        excluded.add(b.content_id)
    elif boundary == 'unresolved':
        doc = doc.model_copy(update={'reading_order_status': 'unresolved'})
    elif boundary == 'table':
        b = b.model_copy(update={'kind': 'table', 'table_id': 'table'})
    doc = doc.model_copy(update={'contents': (a, b)})
    assert all(len(group) == 1 for group in paragraph_groups(doc, excluded))


def test_group_size_limit_preserves_all_contents():
    doc = body_document(['a' * 1300, 'b' * 1300])
    groups = list(paragraph_groups(doc, set()))
    assert [len(g) for g in groups] == [1, 1]
    entries = make_entries(doc)
    assert ''.join(e['primary_text'] for e in entries) == 'a' * 1300 + 'b' * 1300


def test_section_heading_stays_separate_from_body():
    doc = body_document(['1. Maintenance', 'Store in a dry room.', 'Do not seal while wet.'])
    assert doc.sections
    groups = list(paragraph_groups(doc, set()))
    assert [len(g) for g in groups] == [1, 2]


def test_table_interrupts_body_even_when_original_block_matches():
    doc = body_document(['Before table.', 'Table placeholder.', 'After table.'])
    a, b, c = doc.contents
    b = b.model_copy(update={'kind': 'table', 'table_id': 'table'})
    doc = doc.model_copy(update={'contents': (a, b, c)})
    assert [[c.content_id for c in group] for group in paragraph_groups(doc, set())] == [
        [a.content_id], [c.content_id]]


@pytest.fixture
def cell_document(tmp_path):
    source = tmp_path / 'manual.html'
    source.write_text('<h1>Maintenance</h1><table><tr><th>Case</th><th>Description</th>'
        '<th>Action</th></tr><tr><td>Case A</td><td>' + 'Repeated vibration. ' * 80 +
        'Do not restart while wet.</td><td>Dry and inspect.</td></tr>'
        '<tr><td>Case B</td><td>Loose fastener.</td><td>Retighten.</td></tr></table>'
        '<p>Note: disconnect power first.</p>', encoding='utf-8')
    run, _ = prepare_html(source, tmp_path / 'runs')
    return build_from_run(run)


def test_long_cell_search_restores_whole_cell_and_only_same_row(cell_document):
    doc = cell_document
    before = doc.model_dump(mode='json')
    chunks = [e for e in make_entries(doc) if e['kind'] == 'table_cell_chunk']
    assert len(chunks) > 1
    table = doc.physical_tables[0]
    cell = next(c for c in table.cells if c.cell_id == chunks[0]['cell_ids'][0])
    assert ''.join(h['primary_text'] for h in chunks) == cell.original_text
    for hit in chunks:
        assert hit['primary_text'] == cell.original_text[hit['char_start']:hit['char_end']]
        assert hit['row_path'] and hit['column_path'] and hit['span_refs']
    evidence = evidence_pack(doc, [chunks[0]], 'vibration', max_cells=8)
    values = [e['value_raw'] for e in evidence if e['table_id']]
    assert cell.original_text in values and 'Dry and inspect.' in values
    assert 'Retighten.' not in values
    assert any('disconnect power' in e['value_raw'] for e in evidence)
    limited = evidence_pack(doc, [chunks[0]], 'inspect', max_cells=1)
    assert [e['value_raw'] for e in limited if e['table_id']] == [cell.original_text]
    assert doc.model_dump(mode='json') == before


def test_unresolved_long_cell_is_searchable_but_not_promoted(cell_document):
    table = cell_document.table_structure[0]
    mappings = tuple(m.model_copy(update={'evidence_state': 'unresolved'}) for m in table.cell_mappings)
    doc = cell_document.model_copy(update={'table_structure': (
        table.model_copy(update={'cell_mappings': mappings}),)})
    hit = next(e for e in make_entries(doc) if e['kind'] == 'table_cell_chunk')
    assert not hit['lookup_eligible']
    assert not any(e['table_id'] for e in evidence_pack(doc, [hit], 'vibration'))


@pytest.mark.parametrize('field', ['primary_text', 'content_ids', 'source_segments', 'projection_version'])
def test_tampered_body_projection_is_rejected(field):
    doc = body_document(['First sentence.', 'Exception: do not reuse.'])
    hit = copy.deepcopy(make_entries(doc)[0])
    hit[field] = 'tampered'
    with pytest.raises(ValueError, match='SEARCH_PROJECTION_SOURCE_MISMATCH'):
        evidence_pack(doc, [hit], 'reuse')


def test_missing_body_source_ranges_are_rejected():
    doc = body_document(['First sentence.', 'Exception: do not reuse.'])
    hit = make_entries(doc)[0]
    del hit['source_segments']
    with pytest.raises(ValueError, match='SEARCH_PROJECTION_SOURCE_MISMATCH'):
        evidence_pack(doc, [hit], 'reuse')


@pytest.mark.parametrize('field,value', [('row_cell_ids', []), ('cell_ids', ['missing']),
    ('char_start', 9000), ('span_refs', ['missing']), ('primary_text', 'Invented text')])
def test_tampered_cell_scope_is_rejected(cell_document, field, value):
    hit = next(e for e in make_entries(cell_document) if e['kind'] == 'table_cell_chunk')
    hit[field] = value
    with pytest.raises(ValueError, match='SEARCH_PROJECTION_SOURCE_MISMATCH'):
        evidence_pack(cell_document, [hit], 'vibration')
