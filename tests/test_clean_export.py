"""정리된 문서 출력: 원본 기록에서 내용을 잃지 않고, 중복·도형·긴 식별자 없이 펼치는지 확인한다."""
import json
from pathlib import Path

import pytest

from doc_understanding.export.clean import clean_document, label_text
from doc_understanding.export.models import FORMAT, CleanDocument

RUN = Path(__file__).parents[1] / "data/staging-mvp/1a90fc75-f5eb-4c3e-8438-7347ce83547c"


@pytest.mark.parametrize("raw, expected", [
    ("자\n산", "자산"), ("무\n수\n익\n성", "무수익성"), ("운 \n 용", "운용"),
    ("증감\n(B-A)", "증감 (B-A)"), ("2024년(A)", "2024년(A)"), ("  구  분 ", "구 분"), ("가\n", "가"), (None, None),
])
def test_vertical_labels_are_joined_and_other_line_breaks_become_spaces(raw, expected):
    assert label_text(raw) == expected


@pytest.fixture(scope="module")
def document():
    if not RUN.is_dir():
        pytest.skip("needs local documents under data/ that are not in the repository")
    from doc_understanding.parser.assembly.document import build_from_run

    return build_from_run(RUN)


def test_every_cell_node_and_path_of_the_record_is_kept_once(document):
    clean = clean_document(document)
    assert clean["format"] == FORMAT and clean["document"]["facts_published"] is False
    assert len(clean["tables"]) == len(document.physical_tables)
    structured = {t.table_id: t for t in document.table_structure}
    for table, physical in zip(clean["tables"], document.physical_tables):
        source = structured[physical.table_id]
        cells = {c["cell_id"]: c for c in table["grid"]}
        assert len(cells) == len(physical.cells)
        assert [c["text"] for c in table["grid"]] == [c.original_text for c in physical.cells]
        assert [(c["row"], c["col"], c["rowspan"], c["colspan"]) for c in table["grid"]] == [
            (c.row_index, c.column_index, c.rowspan, c.colspan) for c in physical.cells]
        nodes = {n["node_id"]: n for n in table["row_nodes"] + table["column_nodes"]}
        assert len(nodes) == len(source.row_tree) + len(source.column_tree)
        assert [n["label_raw"] for n in table["row_nodes"]] == [n.label_raw for n in source.row_tree]
        # 부모는 같은 표의 노드이고, 경로는 격자의 칸과 그 표의 노드만 가리킨다.
        assert all(n["parent"] is None or n["parent"] in nodes for n in nodes.values())
        assert all(n["cell_id"] in cells for n in nodes.values())
        assert len(table["paths"]) == len(source.cell_mappings)
        for path, mapping in zip(table["paths"], source.cell_mappings):
            assert path["cell_id"] in cells and path["state"] == mapping.evidence_state
            assert (path["row_node"] is None) == (not mapping.row_path)
            assert path["row_node"] is None or nodes[path["row_node"]]["label_raw"] == next(
                n.label_raw for n in source.row_tree if n.node_id == mapping.row_path[-1])
        assert table["state"] == source.analysis_state


def test_reading_order_lists_prose_and_tables_together(document):
    clean = clean_document(document)
    items = [item for page in clean["pages"] for item in page["items"]]
    assert len(items) == len(document.contents)
    tables = [i["table_id"] for i in items if i["kind"] == "table"]
    assert sorted(tables) == list(range(1, len(document.physical_tables) + 1))
    for page in clean["pages"]:
        assert [i["position"] for i in page["items"]] == list(range(1, len(page["items"]) + 1))
    prose = [i for i in items if i["kind"] != "table"]
    assert {i["text"] for i in prose} == {c.text_raw for c in document.contents if c.kind != "table"}
    # 표 안의 글자를 다시 뽑은 줄은 표시가 붙는다(본문으로 읽지 않는다).
    assert sum("inside_tables" in i for i in prose) == sum(bool(c.duplicate_of_table_ids) for c in document.contents)


def test_output_has_no_drawings_or_repeated_long_identifiers_and_is_much_smaller(document):
    text = json.dumps(clean_document(document), ensure_ascii=False)
    record = document.model_dump_json()
    assert "drawing_commands" not in text and "boundary_ids" not in text
    assert text.count(document.document_id) == 1  # 문서 식별자는 한 번만
    assert len(text) * 5 < len(record)
    assert json.loads(text)["links"] is not None


def test_membership_in_the_record_can_be_read_back_from_the_output_alone(document):
    clean = clean_document(document)
    items = {}
    for page in clean["pages"]:
        for item in page["items"]:
            items[("t", item["table_id"]) if item["kind"] == "table" else ("c", item["content_id"])] = (page["page_no"], item)
    sections = {s["section_id"]: s for s in clean["sections"]}
    assert len(sections) == len(document.sections)
    number = {s.section_id: n for n, s in enumerate(sorted(document.sections, key=lambda s: s.reading_order), 1)}
    content_no = {c.content_id: n for n, c in enumerate(document.contents, 1)}
    table_no = {t.table_id: n for n, t in enumerate(document.physical_tables, 1)}
    for content in document.contents:
        key = ("t", table_no[content.table_id]) if content.kind == "table" else ("c", content_no[content.content_id])
        assert items[key][1].get("section_id") == number.get(content.section_id)
    for source in document.sections:
        section = sections[number[source.section_id]]
        assert section["title"] == source.title_raw and section["parent"] == number.get(source.parent_id)
        title = items[("c", section["title_content_id"])][1]
        assert title["section_title"] is True and title["text"] == source.title_raw
        pages = [items[k][0] for k in items if items[k][1].get("section_id") == section["section_id"]]
        assert (section["first_page"], section["last_page"]) == (min(pages), max(pages))


def test_sections_spanning_pages_and_items_without_a_section():
    from types import SimpleNamespace as N

    def ref(page):
        return N(page_number=page, block_id=f"b{page}", span_id=None, bbox=N(x0=0, y0=0, x1=10, y1=10))

    def content(cid, page, order, section, kind="paragraph", **more):
        return N(content_id=cid, section_id=section, kind=kind, text_raw=cid, source_references=(ref(page),),
                 reading_order=order, table_id=more.get("table_id"), duplicate_of_table_ids=(),
                 context_content_ids=more.get("context", ()))

    table = N(table_id="d/p0002/physical000", page_number=2, bounding_box=N(x0=0, y0=0, x1=10, y1=10), rows=(), columns=(), cells=())
    doc = N(document_id="d", document_ir=None, table_structure=(), physical_tables=(table,), context_links=(),
            logical_table_links=(), validation_status="partial",
            sections=(N(section_id="a/section", parent_id=None, title_raw="제1조", reading_order=2, evidence_state="inferred",
                        source_references=(ref(1),)),
                      # 둘째 출처는 다음 쪽에 다시 인쇄된 제목 줄이다.
                      N(section_id="b/section", parent_id="a/section", title_raw="①", reading_order=3, evidence_state="inferred",
                        source_references=(ref(1), ref(2)))),
            contents=(content("front", 1, 1, None), content("a", 1, 2, "a/section"), content("b", 1, 3, "b/section"),
                      content("body", 2, 4, "b/section"),
                      content("t", 2, 5, "b/section", kind="table", table_id=table.table_id, context=("body",))))
    clean = clean_document(doc)
    CleanDocument.model_validate(clean)
    assert clean["sections"] == [
        dict(section_id=1, parent=None, title="제1조", title_content_id=2, first_page=1, last_page=1,
             reprinted_title_content_ids=[], state="inferred"),
        dict(section_id=2, parent=1, title="①", title_content_id=3, first_page=1, last_page=2,
             reprinted_title_content_ids=[4], state="inferred")]
    first, second = (page["items"] for page in clean["pages"])
    assert "section_id" not in first[0] and first[1]["section_title"] is True and first[1]["section_id"] == 1
    assert second[1] == dict(position=2, kind="table", table_id=1, context=[4], section_id=2)


def test_a_value_on_a_later_page_points_at_the_header_cell_it_borrows():
    from types import SimpleNamespace as N

    box = N(x0=0, y0=0, x1=10, y1=10)

    def table(page, texts):
        tid = f"d/p{page:04d}/physical000"
        return N(table_id=tid, page_number=page, bounding_box=box, rows=(1,), columns=tuple(texts),
                 cells=tuple(N(cell_id=f"{tid}/cell{n:04d}", row_index=0, column_index=n, rowspan=1, colspan=1,
                               original_text=t, source_bbox=box, status="resolved") for n, t in enumerate(texts)))

    first, second = table(1, ["순번", "임대주택 주소"]), table(16, ["68", "○○로 1"])
    link = N(source_table_id=first.table_id, target_table_id=second.table_id, kind="page_continuation", status="accepted",
             reason_codes=("CONTINUOUS_ORDINALS",), header_source_cell_ids=tuple(c.cell_id for c in first.cells),
             inherited_row_source_cell_ids=(), candidate_source_table_ids=(),
             paths=(N(cell_id=second.cells[1].cell_id, row_source_cell_ids=(second.cells[0].cell_id,),
                      column_source_cell_ids=(first.cells[1].cell_id,), row_candidate_cell_ids=(), state="inferred",
                      reasons=("CONTINUOUS_ORDINALS",)),))
    doc = N(document_id="d", document_ir=None, table_structure=(), physical_tables=(first, second), context_links=(),
            logical_table_links=(link,), validation_status="partial", sections=(), contents=())
    clean = clean_document(doc)
    CleanDocument.model_validate(clean)
    assert clean["table_links"] == [dict(
        from_table=1, to_table=2, kind="page_continuation", state="accepted", reasons=["CONTINUOUS_ORDINALS"],
        header_cells=[[1, 1], [1, 2]], row_cells=[], candidate_tables=[],
        paths=[dict(cell_id=2, row=[[2, 1]], column=[[1, 2]], state="inferred")])]
    # 값 칸에서 출처를 따라가면 다른 쪽의 머리글 원문에 닿는다.
    path = clean["table_links"][0]["paths"][0]
    table_no, cell_no = path["column"][0]
    header = clean["tables"][table_no - 1]["grid"][cell_no - 1]
    assert (header["text"], clean["tables"][table_no - 1]["page_no"]) == ("임대주택 주소", 1)


def test_output_follows_the_published_contract_and_the_schema_file_is_current(document):
    clean = clean_document(document)
    checked = CleanDocument.model_validate(clean)
    assert checked.format == FORMAT == "tabledoc/0.5"
    # 규격에 없는 항목이 끼면 거부한다.
    with pytest.raises(ValueError):
        CleanDocument.model_validate({**clean, "extra_field": 1})
    with pytest.raises(ValueError):
        CleanDocument.model_validate({**clean, "tables": [{**clean["tables"][0], "state": "confirmed"}]})
    schema = Path(__file__).parents[1] / "schemas" / "clean-document.schema.json"
    assert json.loads(schema.read_text(encoding="utf8")) == CleanDocument.model_json_schema()


def test_contract_needs_no_local_data():
    minimal = dict(format=FORMAT, document=dict(document_id="sha256:x", pages=1, tables=1, validation_status="partial"),
                   pages=[dict(page_no=1, text_source="ocr", ocr=dict(engine="rapidocr", model="korean_PP-OCRv5_mobile", dpi=200),
                               items=[dict(position=1, kind="table", table_id=1)])],
                   tables=[dict(table_id=1, ref="p0001/physical000", page_no=1, bbox=(0, 0, 10, 10), rows=1, columns=1,
                                state="partial", grid=[dict(cell_id=1, row=0, col=0, rowspan=1, colspan=1, role="value",
                                                            text="△280", bbox=(0, 0, 10, 10), confidence=0.99)],
                                paths=[dict(cell_id=1, row_node=None, column_node=None, state="unresolved")])],
                   sections=[dict(section_id=1, parent=None, title="제1조", title_content_id=None, first_page=1,
                                  last_page=2, state="inferred")],
                   links=[], table_links=[])
    assert CleanDocument.model_validate(minimal).tables[0].grid[0].confidence == 0.99


def test_html_public_parent_points_to_container_not_row_label(tmp_path):
    from doc_understanding.parser.assembly.document import build_from_run
    from doc_understanding.parser.extraction.html_adapter import prepare_html

    source = tmp_path / "nested.html"
    source.write_text("<table><tr><td>Schedule</td><td><table><tr><td>10</td></tr></table>"
                      "<table><tr><td>20</td></tr></table></td></tr></table>", encoding="utf8")
    run, _ = prepare_html(source, tmp_path / "runs")
    result = clean_document(build_from_run(run))
    CleanDocument.model_validate(result)
    outer, first, second = result["tables"]
    assert "parent_cell" not in outer
    assert first["parent_cell"] == second["parent_cell"] == {
        "table_id": outer["table_id"], "cell_id": outer["grid"][1]["cell_id"], "state": "observed"
    }
