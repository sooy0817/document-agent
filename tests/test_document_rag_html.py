import json

import pytest

from doc_understanding.parser.assembly.document import build_from_run
from doc_understanding.parser.extraction.html_adapter import html_grid, prepare_html
from doc_understanding.rag.index import cells_for_table, make_entries


def test_static_html_uses_original_contract_order_merges_and_blanks(tmp_path):
    source = tmp_path/'news.html'
    source.write_text('''<html><script>ignore me</script><h1>Economic release</h1>
    <p>Units: million</p><table><caption>Activity</caption><tr><th rowspan="2">Item</th>
    <th colspan="2">2025</th></tr><tr><th>Amount</th><th>Ratio</th></tr>
    <tr><td>Goods</td><td>0</td><td></td></tr></table><p>Note: preliminary.</p></html>''',encoding='utf8')
    run, manifest = prepare_html(source, tmp_path/'runs')
    doc = build_from_run(run)
    assert doc.document_ir.schema_version == 'document-ir/1.0'
    assert source.read_bytes() == (run/'source.html').read_bytes()
    assert doc.physical_tables[0].cells[0].rowspan == 2
    values = cells_for_table(doc, doc.table_structure[0].table_id)
    assert [v['value_raw'] for v in values] == ['0', '']
    assert values[0]['column_path'] == ['2025', 'Amount']
    content = [c for c in doc.contents if not c.duplicate_of_table_ids]
    assert [c.kind for c in content] == ['paragraph','paragraph','paragraph','table','note']
    assert 'ignore me' not in str(make_entries(doc))
    assert manifest['source_sha256'] == doc.document_ir.source_sha256
    assert any(i.code == 'SYNTHETIC_HTML_LAYOUT_NOT_RENDERED' for i in doc.document_ir.issues)


def test_nested_tables_and_illegal_spans_are_isolated(tmp_path):
    source = tmp_path/'bad.html'
    source.write_text('<table><tr><td><table></table></td></tr></table>',encoding='utf8')
    with pytest.raises(ValueError, match='NESTED'):
        prepare_html(source, tmp_path/'runs')
    with pytest.raises(ValueError):
        html_grid([[{'text':'X','rowspan':3,'colspan':1,'header':True}]])


def test_html_title_row_above_th_headers_uses_markup_not_document_exception(tmp_path):
    source=tmp_path/'title.html'
    source.write_text('<table><tr><th colspan="2">Unseen survey, percent</th></tr>'
                      '<tr><th></th><th>Estimate</th></tr><tr><td>North</td><td>1.4</td></tr></table>',encoding='utf8')
    run,_=prepare_html(source,tmp_path/'runs')
    doc=build_from_run(run)
    values=cells_for_table(doc,doc.table_structure[0].table_id)
    assert len(values)==1
    assert values[0]['column_path']==['Estimate']
    assert values[0]['row_path']==['North']
    assert values[0]['lookup_eligible']

def test_nested_html_keeps_direct_owners_and_merged_axes(tmp_path):
    source = tmp_path / 'nested.html'
    source.write_text(
        '<table><tr><th>Schedule</th><td colspan="2">before<table><tr><th>Region</th><th>Item</th><th>Count</th></tr>'
        '<tr><th rowspan="2">East</th><th>Oak</th><td>10</td></tr>'
        '<tr><th>Ash</th><td>20</td></tr></table>after'
        '<table><tr><td><table><tr><td>leaf</td></tr></table></td></tr></table></td></tr></table>',
        encoding='utf8')
    run, _ = prepare_html(source, tmp_path / 'runs')
    doc = build_from_run(run)
    outer, data, wrapper, leaf = doc.physical_tables
    assert outer.cells[1].original_text == 'before after'
    assert outer.cells[1].colspan == 2
    assert data.cells[3].occupied_slots == ((1, 0), (2, 0))
    assert data.source_references[1].cell_id == outer.cells[1].cell_id
    assert leaf.source_references[1].cell_id == wrapper.cells[0].cell_id
    assert [v['row_path'] for v in cells_for_table(doc, data.table_id)] == [['East', 'Oak'], ['East', 'Ash']]
    values = cells_for_table(doc, data.table_id)
    assert values[0]['nested_context']['parent_physical_cell_id'] == outer.cells[1].cell_id
    entries = make_entries(doc)
    row = next(e for e in entries if e.get('table_id') == data.table_id and e['kind'] == 'table_row')
    assert all(term in row['text'] for term in ['East', 'Oak', 'Count', '10', '담긴 위치: Schedule'])
    assert row['parent_physical_cell_id'] == outer.cells[1].cell_id
    assert values[0]['nested_context']['prose'] == []
    assert all('parent_physical_cell_id' not in e for e in entries if e.get('table_id') == outer.table_id)
    links = json.loads((run / 'html-table-links.json').read_bytes())['links']
    assert [(x['parent_cell_id'], x['child_table_id']) for x in links] == [
        (outer.cells[1].cell_id, data.table_id),
        (outer.cells[1].cell_id, wrapper.table_id),
        (wrapper.cells[0].cell_id, leaf.table_id),
    ]


def test_merged_cell_can_cover_an_entire_empty_html_row(tmp_path):
    source = tmp_path / 'merged.html'
    source.write_text('<table><tr><td rowspan="2" colspan="2">East</td></tr><tr></tr>'
                      '<tr><td>Oak</td><td>10</td></tr></table>', encoding='utf8')
    run, _ = prepare_html(source, tmp_path / 'runs')
    table = build_from_run(run).physical_tables[0]
    assert table.cells[0].occupied_slots == ((0, 0), (0, 1), (1, 0), (1, 1))
    assert [cell.row_index for cell in table.cells] == [0, 2, 2]
