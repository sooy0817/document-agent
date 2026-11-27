"""Document assembly/RAG regression; synthetic tests never count as real accuracy."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
GANGDONG = ROOT / 'data/staging-mvp/1a90fc75-f5eb-4c3e-8438-7347ce83547c'


def assembled():
    from doc_understanding.parser.assembly.document import build_from_run
    return build_from_run(GANGDONG)


def test_real_json_preserves_ir_and_all_values():
    doc = assembled()
    assert doc.document_ir.model_dump(mode='json') == json.loads(
        (GANGDONG / 'document-ir.json').read_text(encoding='utf8'))
    assert len(doc.physical_tables) == len(doc.table_structure) == 3
    assert all(c.table_id for c in doc.contents if c.kind == 'table')
    assert doc.fact_publication_allowed is False
    from doc_understanding.parser.assembly.document import validate_document
    validate_document(doc)


def test_numbered_hierarchy_is_candidate_and_combined_label_is_preserved():
    from doc_understanding.rag.index import cells_for_table
    doc = assembled()
    table = next(t for t in doc.table_structure if t.page_number == 2)
    values = cells_for_table(doc, table.table_id)
    cash = next(v for v in values if v['row_path'][-1] == '1.현금'
                and v['column_path'] == ['2025년(B)', '금액'])
    assert cash['row_path'] == ['자\n산', 'Ⅰ.유동자산', '1.현금']
    assert cash['value_raw'] == '963'
    assert cash['semantic_status'] == 'unvalidated_candidate'
    assert any('3.재고·생물자산' in v['row_path'] for v in values)
    assert any(v['value_raw'] == '' and v['value_kind'] == 'blank' for v in values)
    assert any(v['value_raw'] == '-111' for v in values)


def test_search_is_derived_and_body_table_context_is_connected():
    from doc_understanding.rag.index import make_entries
    doc = assembled()
    entries = make_entries(doc)
    assert any(e['kind'] == 'paragraph' for e in entries)
    hit = next(e for e in entries if e['kind'] == 'table' and e['page'] == 2)
    assert '재무현황' in hit['text'] and '유동자산' in hit['text'] and '2025' in hit['text']
    assert hit['context_content_ids']


def test_invalid_reference_and_cycle_are_rejected():
    from doc_understanding.parser.assembly.document import validate_document
    doc = assembled()
    bad = doc.contents[0].model_copy(update={'table_id': 'missing', 'kind': 'table'})
    with pytest.raises(ValueError):
        validate_document(doc.model_copy(update={'contents': (bad,) + doc.contents[1:]}))
    table = doc.table_structure[0]
    n = table.row_tree[0]
    badtable = table.model_copy(update={'row_tree': (
        n.model_copy(update={'parent_id': n.node_id}),) + table.row_tree[1:]})
    with pytest.raises(ValueError):
        validate_document(doc.model_copy(update={'table_structure': (badtable,) + doc.table_structure[1:]}))


def test_answer_cannot_invent_values_citations_or_promote_candidates():
    from doc_understanding.rag.generation.answer import check_selection, render_answer
    evidence = [{'evidence_id': 'E1', 'value_raw': '0', 'value_kind': 'number',
                 'row_path': ['X'], 'column_path': ['2025'], 'page': 1,
                 'table_id': 'T', 'cell_id': 'C', 'source_name': 's.pdf', 'lookup_eligible': True,
                 'document_id': 'D', 'unit_context': [], 'notes': [],
                 'semantic_status': 'unvalidated_candidate'}]
    good = {'evidence_ids': ['E1'], 'values_raw': ['0'], 'abstained': False}
    check_selection(good, evidence)
    assert '미검증' in render_answer(good, evidence)
    for bad in ({**good, 'values_raw': ['1']}, {**good, 'evidence_ids': ['made-up']},
                {**good, 'abstained': True}):
        with pytest.raises(ValueError):
            check_selection(bad, evidence)


def test_blank_zero_and_sign_parser():
    from doc_understanding.rag.index import parse_value
    assert parse_value('') == ('blank', None)
    assert parse_value('0') == ('number', '0')
    assert parse_value('△2,170') == ('triangle_decrease_candidate', '-2170')
    assert parse_value('-') == ('hyphen', None)
    assert parse_value('-111') == ('number', '-111')
    assert parse_value(None) == ('unavailable', None)


def test_unavailable_hierarchy_labels_remain_searchable_but_not_answerable():
    from doc_understanding.rag.index import make_entries, cells_for_table
    doc=assembled()
    table=doc.table_structure[0]
    broken=table.column_tree[0].model_copy(update={'label_raw':None,'evidence_state':'unresolved'})
    table=table.model_copy(update={'column_tree':(broken,)+table.column_tree[1:]})
    doc=doc.model_copy(update={'table_structure':(table,)+doc.table_structure[1:]})
    assert any('[unavailable label]' in e['text'] for e in make_entries(doc))
    assert any(not v['lookup_eligible'] for v in cells_for_table(doc,table.table_id))
    # 읽지 못한 라벨이 있어도 표 목록·재정렬 요약은 만들어진다(스캔 문서에서 죽지 않는다).
    from doc_understanding.rag.agentic.tools import table_outline
    from doc_understanding.rag.agentic.rerank import describe
    assert any('[unavailable label]' in c for c in table_outline(doc,table.table_id)['columns'])
    assert describe(dict(row_path=[None,'a b'],column_path=[None],value_raw='1'))['row']==' > a b'


def test_document_label_names_the_document_in_entries_and_evidence():
    from types import SimpleNamespace as NS
    from doc_understanding.rag.evidence.document import document_label, document_title
    from doc_understanding.rag.index import make_entries, cells_for_table
    from doc_understanding.rag.agentic.rerank import describe
    doc=assembled()
    label=document_label(doc)
    assert label.startswith(doc.document_ir.source_name) and document_title(doc)
    entries=make_entries(doc)
    assert all(e['text'].startswith(label) for e in entries if e['kind'] in ('table','table_row','paragraph'))
    cell=cells_for_table(doc,doc.table_structure[0].table_id)[0]
    assert cell['document_label']==label and describe(cell)['document']==label
    # 제목 = 첫 쪽에서 글자가 가장 큰 글, 그다음 첫 글. 글자가 없는 문서(스캔)는 파일 이름만.
    block=lambda text,size: NS(text_raw=text,spans=[NS(font_size=size)])
    fake=lambda blocks: NS(document_ir=NS(source_name='N11.pdf',pages=[NS(blocks=blocks)]))
    assert document_label(fake([block('은행  심의필 2013',10.0),block('노후보장정기예금 상품설명서',20.0),block('1 상품 개요',12.0)]))         == 'N11.pdf | 노후보장정기예금 상품설명서 | 은행 심의필 2013'
    assert document_label(fake([NS(text_raw='',spans=[]),NS(text_raw=None,spans=[])])) == 'N11.pdf'
    assert document_label(NS(document_ir=NS(source_name='a.pdf',pages=[]))) == 'a.pdf'


def test_all_160_real_page2_values_against_agent_visual_transcription():
    from doc_understanding.rag.index import cells_for_table
    reference=json.loads((ROOT/'tests/fixtures/document_rag/gangdong-p2.agent-reference.json').read_text(encoding='utf8'))
    doc=assembled()
    assert doc.document_ir.source_sha256==reference['source_sha256']
    table=next(t for t in doc.table_structure if t.page_number==reference['page'])
    values=cells_for_table(doc,table.table_id)
    def normalize(labels):
        return tuple(x.replace('\n','') for x in labels)
    actual={(normalize(v['row_path']),tuple(v['column_path'])):v['value_raw'] for v in values}
    expected={(normalize(row['path']),tuple(col)):raw for row in reference['rows']
              for col,raw in zip(reference['columns'],row['values'],strict=True)}
    assert len(actual)==len(values)==len(expected)==160
    assert actual==expected
    note=next(v for v in values if v['row_path'][-1]==reference['note']['target_leaf'])['notes']
    assert len(note)==1 and note[0]['scope']=='row' and '대손충당금' in note[0]['text_raw']
    cash=next(v for v in values if v['row_path'][-1]=='1.현금')
    assert not cash['notes']
