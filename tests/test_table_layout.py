"""표 배치 명세(TableLayout): 머리글 행 수와 행별 라벨 깊이로 역할·경로를 만든다. 모델 호출 없음."""
import pytest

from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer
from doc_understanding.parser.structure.layout import TableLayout
from tests.test_question_agent import reviewed_kb


@pytest.fixture(scope="module")
def kb():
    return reviewed_kb()


def physical(doc, suffix):
    return next(t for t in doc.physical_tables if t.table_id.endswith(suffix))


def analyze(doc, suffix, layout):
    pt = physical(doc, suffix)
    return pt, SemanticTableAnalyzer().analyze(pt, layout=layout)


def paths(pt, st):
    nodes = {n.node_id: " ".join((n.label_raw or "").split()) for n in st.row_tree + st.column_tree}
    cells = {c.cell_id: c for c in pt.cells}
    return {(cells[m.cell_id].row_index, cells[m.cell_id].column_index):
            ([nodes[n] for n in m.row_path], [nodes[n] for n in m.column_path], m.evidence_state)
            for m in st.cell_mappings}


def roles(pt, st):
    cells = {c.cell_id: c for c in pt.cells}
    return {(cells[r.cell_id].row_index, cells[r.cell_id].column_index): r.role for r in st.cell_roles}


def test_default_analysis_is_unchanged_and_uniform_layout_matches_it(kb):
    pt = physical(kb, "p0005/physical002")
    old = SemanticTableAnalyzer().analyze(pt, header_rows=1, stub_columns=1)
    new = SemanticTableAnalyzer().analyze(pt, layout=TableLayout.uniform(len(pt.rows), 1, 1))
    assert paths(pt, old) == paths(pt, new) and roles(pt, old) == roles(pt, new)
    hypothesis = next(a for a in new.ambiguities if a.code == "LAYOUT_HYPOTHESIS")
    assert any(c.startswith("row_stub_depths=") for c in hypothesis.candidate_ids)  # 적용한 배치가 기록된다


def test_per_row_label_depth_maps_one_and_two_level_rows(kb):
    # KB 1쪽 상품 개요: '구 분 | 내 용(2열 병합)', 대부분 1단 행, 계약기간만 2단(고정·변동금리형).
    pt = physical(kb, "p0001/physical002")
    depths = (0, 1, 1, 1, 2, 2, 1, 1, 1)
    pt, st = analyze(kb, "p0001/physical002", TableLayout(header_rows=1, row_stub_depths=depths))
    got, role = paths(pt, st), roles(pt, st)
    assert role[0, 0] == "corner" and role[0, 1] == "column_header"  # 라벨 열에 걸친 머리글
    assert got[1, 1] == (["취급대상"], ["내 용"], "inferred")
    assert got[4, 2] == (["계약기간", "고정금리형"], ["내 용"], "inferred")
    assert got[5, 2] == (["계약기간", "변동금리형"], ["내 용"], "inferred")
    assert "unknown" not in role.values()
    assert any("row_stub_depths=0,1,1,1,2,2,1,1,1" in c for a in st.ambiguities if a.code == "LAYOUT_HYPOTHESIS"
               for c in a.candidate_ids)
    assert not any(i.severity == "error" for i in st.issues)


def test_header_spanning_label_and_value_columns_joins_the_column_path(kb):
    # KB 1쪽 금리표: '계약기간'이 월구간(라벨 열)과 일구간(값 열)에 걸친다. 변동금리형 행 라벨은 두 열 병합.
    pt = physical(kb, "p0001/physical003")
    depths = (0, 0) + (2,) * 10 + (3, 3)
    pt, st = analyze(kb, "p0001/physical003", TableLayout(header_rows=2, row_stub_depths=depths))
    got = paths(pt, st)
    assert got[2, 2][:2] == (["고정 금리형", "1개월∼ 3개월 미만"], ["계약기간", "일구간"])
    assert got[12, 3] == (["변동 금리형", "1년(3개월 회전)"], ["이자율 (연%)"], "inferred")
    assert got[13, 3][0] == ["변동 금리형", "1년(6개월 회전)"]  # 충돌 없음


def test_invalid_layout_is_rejected(kb):
    pt = physical(kb, "p0005/physical002")
    for layout in (TableLayout(header_rows=1, row_stub_depths=(1,)),                      # 행 수 불일치
                   TableLayout.uniform(len(pt.rows), len(pt.rows), 1),                    # 본문 없음
                   TableLayout.uniform(len(pt.rows), 1, len(pt.columns))):                # 값 열 없음
        with pytest.raises(ValueError, match="INVALID_TABLE_LAYOUT"):
            SemanticTableAnalyzer().analyze(pt, layout=layout)
    with pytest.raises(ValueError, match="INVALID_SOURCE_LAYOUT_HINT"):
        SemanticTableAnalyzer().analyze(pt, header_rows=1, stub_columns=1,
                                        layout=TableLayout.uniform(len(pt.rows), 1, 1))


def test_cell_outside_the_row_range_is_diagnosed_not_raised(kb):
    pt = physical(kb, "p0005/physical002")
    last = max(pt.cells, key=lambda c: (c.row_index, c.column_index))
    beyond = tuple((r + len(pt.rows), c) for r, c in last.occupied_slots)
    broken = pt.model_copy(update=dict(cells=tuple(
        c.model_copy(update=dict(occupied_slots=beyond)) if c.cell_id == last.cell_id else c for c in pt.cells)))
    for kwargs in ({}, dict(layout=TableLayout.uniform(len(pt.rows), 1, 1))):
        st = SemanticTableAnalyzer().analyze(broken, **kwargs)
        assert st.analysis_state == "invalid" and any(i.code == "INVALID_SOURCE_OCCUPANCY" for i in st.issues)


# --- 2단계: 라벨 경계에 걸친 칸에서 행별 라벨 깊이를 정한다(규칙) ---


def raw_kb():
    from doc_understanding.domain.pipeline_contracts import CanonicalDocument
    from tests.test_question_agent import KB_DOCUMENT
    if not KB_DOCUMENT.is_file():
        pytest.skip("local KB baseline document is not available")
    return CanonicalDocument.model_validate_json(KB_DOCUMENT.read_bytes())


def test_derive_layout_reads_value_and_label_spans(kb):
    from doc_understanding.parser.structure.layout import derive_layout
    # 값이 오른쪽 끝까지 닿는 병합이면 그 행의 깊이가 줄고, 오른쪽에 칸이 남는 병합이면 라벨이 넓어진다.
    assert derive_layout(physical(kb, "p0001/physical002"), 1, 2).row_stub_depths == (2, 1, 1, 1, 2, 2, 1, 1, 1)
    assert derive_layout(physical(kb, "p0001/physical003"), 2, 2).row_stub_depths[-2:] == (3, 3)
    assert derive_layout(physical(kb, "p0005/physical002"), 1, 1) is None  # 걸친 칸이 없으면 예전 추정으로 충분


def test_review_applies_row_depths_only_where_the_header_is_supported():
    from doc_understanding.agents.structure_review.review import review_document
    doc = raw_kb()
    reviewed, report = review_document(doc)
    by = {d["table_id"].split("/", 1)[1]: d for d in report["decisions"] if d["aspect"] == "layout"}
    assert by["p0001/physical002"]["status"] == "decided" and "ROW_DEPTH_FROM_SPANS" in by["p0001/physical002"]["supports"]
    # 머리글 근거가 없는 표: 첫 행 칸이 표를 담고 있으면(5·6쪽) 그 행은 머리글일 수 없으므로 머리글 없는 표로 결정한다.
    # 후보(머리글 있음·없음)는 판정에 남는다.
    for box in ("p0005/physical000", "p0006/physical000"):
        assert by[box]["reason"] == "FIRST_ROW_HOLDS_TABLE" and by[box]["choice"]["header_rows"] == 0
        assert [c["header_rows"] for c in by[box]["candidates"]] == [1, 0]
    p6 = physical(reviewed, "p0006/physical000")
    s6 = next(t for t in reviewed.table_structure if t.table_id == p6.table_id)
    assert paths(p6, s6)[6, 2] == (["예금자보호 여부", "확정급여형 퇴직연금(비보호)"], [], "inferred")
    pt = physical(reviewed, "p0001/physical003")
    st = next(t for t in reviewed.table_structure if t.table_id == pt.table_id)
    assert paths(pt, st)[2, 2][1] == ["계약기간", "일구간"] and paths(pt, st)[12, 3][2] == "inferred"
    assert reviewed.physical_tables == doc.physical_tables and reviewed.document_ir == doc.document_ir


def test_row_depths_do_not_override_a_header_conflict():
    from doc_understanding.agents.structure_review.review import review_document
    doc = raw_kb()
    tables = []
    for t in doc.physical_tables:
        if t.table_id.endswith("p0001/physical002"):  # 첫 머리글 칸을 숫자로 바꾸면 머리글 해석이 충돌한다
            t = t.model_copy(update=dict(cells=tuple(
                c.model_copy(update=dict(original_text="2025")) if (c.row_index, c.column_index) == (0, 0) else c
                for c in t.cells)))
        tables.append(t)
    changed = doc.model_copy(update=dict(physical_tables=tuple(tables)))
    structure = SemanticTableAnalyzer().analyze(tables[2] if tables[2].table_id.endswith("physical002") else
                                                next(t for t in tables if t.table_id.endswith("p0001/physical002")))
    changed = changed.model_copy(update=dict(table_structure=tuple(
        structure if s.table_id == structure.table_id else s for s in changed.table_structure)))
    _, report = review_document(changed)
    held = next(d for d in report["decisions"] if d["table_id"].endswith("p0001/physical002") and d["aspect"] == "layout")
    assert held["status"] == "ambiguous" and held["reason"] == "HEADER_CONFLICT"
    assert not any(a["table_id"].endswith("p0001/physical002") and a["aspect"] == "layout" for a in report["applied"])


# --- 3단계: 한쪽 경로만 있는 값, 같은 경로의 나란한 값 묶음 ---


def test_headerless_layout_gives_row_only_paths(kb):
    # KB 6쪽: 머리글 없는 라벨 | 내용 표. 첫 행도 데이터다.
    from doc_understanding.parser.structure.layout import derive_layout
    pt = physical(kb, "p0006/physical000")
    layout = derive_layout(pt, 0, 2)
    assert layout.header_rows == 0 and layout.row_stub_depths == (1, 1, 1, 1, 1, 2, 2)
    st = SemanticTableAnalyzer().analyze(pt, layout=layout)
    got = paths(pt, st)
    assert got[1, 1] == (["거래방법"], [], "inferred")
    assert got[6, 2] == (["예금자보호 여부", "확정급여형 퇴직연금(비보호)"], [], "inferred")
    assert got[0, 1][0] == ["만기후처리방법"] and not any(i.severity == "error" for i in st.issues)


def test_missing_axis_is_still_unresolved_when_the_layout_has_that_axis(kb):
    pt = physical(kb, "p0006/physical000")
    st = SemanticTableAnalyzer().analyze(pt)  # 예전 추정: 머리글 1행. 2단 행 값의 열 경로가 비어 있다.
    assert paths(pt, st)[5, 2][1:] == ([], "unresolved")


def test_one_axis_values_are_evidence_and_same_path_neighbours_form_a_group():
    from doc_understanding.domain.pipeline_contracts import CanonicalDocument
    from doc_understanding.rag.evidence.cells import cells_for_table
    from doc_understanding.runtime.paths import ROOT
    source = ROOT / "work/document-explorer/ff772978ceb446498868945a347df6bd/document.json"
    if not source.is_file():
        pytest.skip("local Suhyup document is not available")
    doc = CanonicalDocument.model_validate_json(source.read_bytes())
    pt = physical(doc, "p0001/physical000")
    # '구 분 | 내 용' 머리글 1행, 라벨 1열. 마지막 행은 '해당' | 설명 두 칸이 같은 경로다.
    st = SemanticTableAnalyzer().analyze(pt, header_rows=1, stub_columns=1)
    changed = doc.model_copy(update=dict(table_structure=tuple(
        st if s.table_id == st.table_id else s for s in doc.table_structure)))
    cells = {(c["source"]["page_number"], c["cell_id"]): c for c in cells_for_table(changed, pt.table_id)}
    by_text = {" ".join(c["value_raw"].split())[:2]: c for c in cells.values()}
    pair = by_text["해당"]
    assert pair["mapping_state"] == "unresolved" and pair["lookup_eligible"] and len(pair["value_group"]) == 2
    assert all(c["lookup_eligible"] or c["container_duplicate"] for c in cells.values())
    # 한쪽 경로만 있는 값(머리글 없는 표)은 근거가 된다.
    kb_doc = raw_kb()
    kpt = physical(kb_doc, "p0007/physical000")
    from doc_understanding.parser.structure.layout import TableLayout as Layout
    kst = SemanticTableAnalyzer().analyze(kpt, layout=Layout.uniform(len(kpt.rows), 0, 1))
    kb_doc = kb_doc.model_copy(update=dict(table_structure=tuple(
        kst if s.table_id == kst.table_id else s for s in kb_doc.table_structure)))
    rows = cells_for_table(kb_doc, kpt.table_id)
    assert len(rows) == 2 and all(c["lookup_eligible"] and c["row_path"] and not c["column_path"] for c in rows)


def test_group_member_carries_its_neighbours_even_when_selected_alone():
    from doc_understanding.domain.pipeline_contracts import CanonicalDocument
    from doc_understanding.rag.evidence.cells import cells_for_table
    from doc_understanding.rag.generation.narrative import build_catalog
    from doc_understanding.runtime.paths import ROOT
    source = ROOT / "work/document-explorer/ff772978ceb446498868945a347df6bd/document.json"
    if not source.is_file():
        pytest.skip("local Suhyup document is not available")
    doc = CanonicalDocument.model_validate_json(source.read_bytes())
    pt = physical(doc, "p0001/physical000")
    st = SemanticTableAnalyzer().analyze(pt, header_rows=1, stub_columns=1)
    doc = doc.model_copy(update=dict(table_structure=tuple(
        st if s.table_id == st.table_id else s for s in doc.table_structure)))
    alone = next(c for c in cells_for_table(doc, pt.table_id) if c["value_raw"].strip() == "해당")
    assert len(alone["group_values"]) == 1 and "예금자보호법" in alone["group_values"][0]["text_raw"]
    catalog = build_catalog([dict(alone, evidence_id="E1")])
    sibling = next(s for s in catalog if s["kind"] == "parent_text" and s.get("cell_id") == alone["group_values"][0]["cell_id"])
    assert sibling["carrier_evidence_id"] == "E1" and sibling["source_references"]
