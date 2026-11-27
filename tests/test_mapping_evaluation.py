"""셀 매핑 평가: 테스트셋 계약·저장, 셀 보기(근거 사용 이유), 채점, 검토 화면 API. 모델 호출 없음."""
import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from doc_understanding.evaluation.mapping.dataset import ExpectedCell, ExpectedTable, MappingSet, compact, load, save
from doc_understanding.evaluation.mapping.score import score, score_table
from doc_understanding.rag.evidence.inspect import document_views, table_view
from tests.test_question_agent import _table, reviewed_kb


def cell_at(view, r, c):
    return next(x for x in view["cells"] if (x["r"], x["c"]) == (r, c))


def expected(view, r, c, role="value", row=(), col=(), **kw):
    return ExpectedCell(r=r, c=c, text=compact(cell_at(view, r, c)["text"]), role=role,
                        row_path=list(row), column_path=list(col), **kw)


@pytest.fixture(scope="module")
def kb():
    return reviewed_kb()


def test_labels_are_cleaned_and_save_round_trips_one_cell_per_line(tmp_path):
    cell = ExpectedCell(r=1, c=2, text="x", role="value", row_path=["계약\n기간 "], column_path=["내  용"])
    assert cell.row_path == ["계약 기간"] and cell.column_path == ["내 용"]
    data = MappingSet(name="m", status="draft", reviewed_by_human=False, documents={"d": "sha"},
                      tables=[ExpectedTable(doc="d", table="p0001/physical000",
                                            cells=[cell, ExpectedCell(r=0, c=0, role="corner", reviewed=True)])])
    save(data, tmp_path / "m.json")
    text = (tmp_path / "m.json").read_text(encoding="utf-8")
    assert '{"r": 0, "c": 0, "role": "corner", "reviewed": true}' in text  # 정렬 후 한 줄
    assert load(tmp_path / "m.json").tables[0].cells[1].row_path == ["계약 기간"]
    assert not load(tmp_path / "m.json").reviewed_by_human  # 모든 셀을 확인해야 검토 완료다


@pytest.fixture(scope="module")
def kb_unreviewed():
    """구조 검토 전 문서(분석기의 예전 추정 그대로). 근거에서 빠지는 이유를 보여 주는 예로 쓴다."""
    from doc_understanding.domain.pipeline_contracts import CanonicalDocument
    from tests.test_question_agent import KB_DOCUMENT
    if not KB_DOCUMENT.is_file():
        pytest.skip("local KB baseline document is not available")
    return CanonicalDocument.model_validate_json(KB_DOCUMENT.read_bytes())


def test_view_explains_why_a_value_is_not_evidence(kb, kb_unreviewed):
    # 검토 전 KB 1쪽 상품 개요: 표 전체에 라벨 2열을 두어 2단 행 값의 열 경로가 비고 근거에서 빠진다.
    view = table_view(kb_unreviewed, _table(kb_unreviewed, "p0001/physical002"))
    value = cell_at(view, 4, 2)
    assert value["role"] == "value" and not value["eligible"]
    assert "COLUMN_PATH_MISSING" in value["blockers"] and value["row_path"][0].strip() == "계약기간"
    # 1단 행의 값은 머리글·라벨 경계에 걸쳐 역할이 정해지지 않는다.
    assert cell_at(view, 1, 1)["blockers"] == ["CELL_ROLE_UNRESOLVED"]
    # KB 1쪽 상품 개요: 행별 라벨 깊이 배치(구조 검토 규칙)로 1단·2단 행 값이 모두 근거가 된다.
    fixed = table_view(kb, _table(kb, "p0001/physical002"))
    assert cell_at(fixed, 4, 2)["eligible"] and cell_at(fixed, 1, 1)["eligible"]
    good = cell_at(table_view(kb, _table(kb, "p0006/physical001")), 1, 1)
    assert good["eligible"] and good["blockers"] == [] and good["text"] == "12개월"


def test_score_separates_exact_wrong_withheld_and_mismatch(kb):
    tid = _table(kb, "p0006/physical001")
    view = table_view(kb, tid)
    row, col = cell_at(view, 1, 1)["row_path"], cell_at(view, 1, 1)["column_path"]
    t = ExpectedTable(doc="kb", table="p0006/physical001", cells=[
        expected(view, 1, 1, row=row, col=col),                      # 정확
        expected(view, 1, 2, row=["재예치기간"], col=["다른 열"]),      # 근거로 쓰이지만 경로가 틀림
        expected(view, 0, 0, role="corner"),
        ExpectedCell(r=1, c=3, text="원문이바뀜", role="value", row_path=["x"]),
    ])
    rows, _ = score_table(t, view)
    by = {(r["r"], r["c"]): r for r in rows}
    assert by[1, 1]["exact"] and by[1, 2]["wrong_confirmed"] and by[0, 0]["role_correct"]
    assert by[1, 3]["status"] == "cell_mismatch"
    data = MappingSet(name="m", status="draft", reviewed_by_human=False, documents={"kb": kb.document_id}, tables=[t])
    s = score(data, document_views({kb.document_id: kb}))["summary"]
    assert s["targets"] == 3 and s["exact"]["rate"] == 0.333 and s["wrong_confirmed"] == 1 and s["cell_mismatch"] == 1
    assert s["exact_matched"]["rate"] == 0.5


def test_expected_value_with_a_missing_column_path_counts_as_not_answerable(kb_unreviewed):
    kb = kb_unreviewed
    view = table_view(kb, _table(kb, "p0001/physical002"))
    t = ExpectedTable(doc="kb", table="p0001/physical002",
                      cells=[expected(view, 4, 2, row=["계약기간", "고정금리형"], col=["내 용"])])
    data = MappingSet(name="m", status="draft", reviewed_by_human=False, documents={"kb": kb.document_id}, tables=[t])
    result = score(data, document_views({kb.document_id: kb}))
    assert result["summary"]["answerable"]["rate"] == 0.0 and result["blockers"]["COLUMN_PATH_MISSING"] == 1


def test_missing_document_is_reported_not_scored(kb):
    data = MappingSet(name="m", status="draft", reviewed_by_human=False, documents={"x": "sha256:none"},
                      tables=[ExpectedTable(doc="x", table="p0001/physical000", cells=[])])
    assert score(data, {})["tables"][0]["missing"]


@pytest.fixture
def client(tmp_path, kb):
    from doc_understanding.web.mapping_routes import router
    view = table_view(kb, _table(kb, "p0006/physical001"))
    data = MappingSet(name="m", status="draft", reviewed_by_human=False, documents={"kb": kb.document_id},
                      tables=[ExpectedTable(doc="kb", table="p0006/physical001",
                                            cells=[expected(view, 1, 1, row=["재예치기간"], col=["x"]),
                                                   expected(view, 0, 0, role="corner")])])
    path = tmp_path / "set.json"
    save(data, path)

    def authorize(request):
        if request.headers.get("x-demo-token") != "t":
            raise HTTPException(403)
    static = tmp_path / "static"
    static.mkdir()
    (static / "mapping.html").write_text("<script>window.token='__TOKEN__'</script>", encoding="utf8")
    app = FastAPI()
    app.include_router(router(authorize, static, lambda: "t", lambda: {kb.document_id: kb}, set_path=path))
    return TestClient(app), path


def test_routes_show_analysis_and_expected_and_save_reviews(client):
    http, path = client
    assert "window.token='t'" in http.get("/mapping").text
    overview = http.get("/api/mapping").json()
    assert overview["tables"][0]["summary"]["wrong_confirmed"] == 1
    table = http.get("/api/mapping/table", params=dict(doc="kb", table="p0006/physical001")).json()
    cell = next(c for c in table["cells"] if (c["r"], c["c"]) == (1, 1))
    assert cell["eligible"] and table["page"] == 6 and len(table["expected"]) == 2
    fixed = [dict(c, column_path=cell["column_path"], reviewed=True) if (c["r"], c["c"]) == (1, 1) else c
             for c in table["expected"]]
    body = dict(doc="kb", table="p0006/physical001", cells=fixed)
    assert http.put("/api/mapping/table", json=body).status_code == 403
    saved = http.put("/api/mapping/table", json=body, headers={"x-demo-token": "t"}).json()
    assert saved == dict(saved=2, reviewed=1, cells=2)
    stored = load(path).tables[0].cells
    assert stored[1].reviewed and stored[1].column_path == [" ".join(cell["column_path"][0].split())]
    assert http.get("/api/mapping").json()["tables"][0]["summary"]["exact"]["rate"] == 1.0


def test_routes_refuse_changing_cell_coordinates_or_text(client):
    http, path = client
    before = path.read_text(encoding="utf-8")
    table = http.get("/api/mapping/table", params=dict(doc="kb", table="p0006/physical001")).json()
    bad = [dict(c, text="다른원문") for c in table["expected"]]
    r = http.put("/api/mapping/table", json=dict(doc="kb", table="p0006/physical001", cells=bad),
                 headers={"x-demo-token": "t"})
    assert r.status_code == 422 and path.read_text(encoding="utf-8") == before
    assert http.get("/api/mapping/table", params=dict(doc="kb", table="p0009/physical000")).status_code == 404


def test_label_role_drops_paths_on_save(client):
    http, path = client
    table = http.get("/api/mapping/table", params=dict(doc="kb", table="p0006/physical001")).json()
    cells = [dict(c, role="row_label") if (c["r"], c["c"]) == (1, 1) else c for c in table["expected"]]
    http.put("/api/mapping/table", json=dict(doc="kb", table="p0006/physical001", cells=cells),
             headers={"x-demo-token": "t"})
    stored = json.loads(path.read_text(encoding="utf-8"))["tables"][0]["cells"]
    assert stored[1]["role"] == "row_label" and "row_path" not in stored[1]


# --- 내용을 담는 칸(셀 안이 하나의 내용)과 칸 안에 담긴 표 ---


def test_container_cell_holds_text_and_tables_in_source_order(kb):
    from doc_understanding.rag.evidence.inspect import containment
    info = containment(kb)
    view = table_view(kb, _table(kb, "p0002/physical000"), info)
    box = cell_at(view, 0, 1)["container"]
    kinds = [i["kind"] for i in box["items"]]
    tables = [i["table"].split("/", 1)[1] for i in box["items"] if i["kind"] == "table"]
    assert tables == ["p0002/physical001", "p0002/physical002"] and kinds[0] == "text" and kinds.count("text") > 5
    assert kinds.index("table") < kinds.index("table", kinds.index("table") + 1)  # 문장이 두 표 사이에 있다
    # 쪽 전체 테두리처럼 칸 하나로 좁혀지지 않는 연결은 담는 칸이 아니다.
    assert not any(c["container"] for c in table_view(kb, _table(kb, "p0001/physical000"), info)["cells"])


def test_nested_table_evidence_carries_where_it_is_held(kb):
    from doc_understanding.rag.evidence.cells import cells_for_table
    view = table_view(kb, _table(kb, "p0003/physical002"))
    chain = [(p["table"].split("/", 1)[1], p["r"], p["c"]) for p in view["parents"]]
    assert chain == [("p0003/physical000", 0, 1), ("p0003/physical001", 2, 2)]  # 바깥 → 안쪽
    assert [x.split()[0] for x in view["parents"][1]["label"]] == ["최초", "회전기간이"]
    assert view["context_carried"] is True
    # 근거: 담은 칸 라벨 경로(바깥 → 안쪽)와 바깥 칸 문장('[변동금리형]')이 원문 그대로 함께 간다.
    context = cells_for_table(kb, _table(kb, "p0003/physical002"))[0]["nested_context"]
    assert [p["label"].split()[0] for p in context["path"]] == ["일반중도해지", "최초"]
    assert any("변동금리형" in p["text_raw"] for p in context["outer_prose"])


def test_score_counts_containers_and_parents(kb):
    from doc_understanding.evaluation.mapping.dataset import CellRef, content_refs
    views = document_views({kb.document_id: kb})
    outer = views[_table(kb, "p0002/physical000")]
    box = cell_at(outer, 0, 1)
    tables = [
        ExpectedTable(doc="kb", table="p0002/physical000", cells=[
            expected(outer, 0, 1, role="container", content=content_refs(box["container"]["items"]))]),
        ExpectedTable(doc="kb", table="p0002/physical002", parents=[CellRef(table="p0002/physical000", r=0, c=1)],
                      cells=[]),
        ExpectedTable(doc="kb", table="p0002/physical001", parents=[CellRef(table="p0001/physical002", r=7, c=1)],
                      cells=[]),
    ]
    data = MappingSet(name="m", status="draft", reviewed_by_human=False, documents={"kb": kb.document_id}, tables=tables)
    s = score(data, views)["summary"]
    assert s["containers"] == 1 and s["container_found"]["rate"] == 1.0 and s["content_exact"]["rate"] == 1.0
    assert s["nested_tables"] == 2 and s["parent_correct"]["rate"] == 0.5 and s["context_carried"]["rate"] == 1.0
    # 순서가 다르면 내용 불일치다.
    tables[0].cells[0].content = list(reversed(tables[0].cells[0].content))
    assert score(data, views)["summary"]["content_exact"]["rate"] == 0.0


def test_saving_keeps_container_content_from_the_set(client):
    http, path = client
    data = load(path)
    t = data.tables[0]
    from doc_understanding.evaluation.mapping.dataset import ContentRef
    t.cells[1] = t.cells[1].model_copy(update=dict(role="container", content=[ContentRef(kind="text", text="가")]))
    save(data, path)
    table = http.get("/api/mapping/table", params=dict(doc="kb", table="p0006/physical001")).json()
    cells = [dict(c, content=[], reviewed=True) for c in table["expected"]]  # 화면이 내용을 비워 보내도
    http.put("/api/mapping/table", json=dict(doc="kb", table="p0006/physical001", cells=cells),
             headers={"x-demo-token": "t"})
    assert load(path).tables[0].cells[1].content == [ContentRef(kind="text", text="가")]


# --- 중복 제외: 담는 칸 원문은 안쪽 표·문장과 같은 글자라 근거·검색에서 뺀다 ---


def test_covered_container_text_is_not_value_evidence_or_search_entry(kb):
    from doc_understanding.rag.evidence.cells import cells_for_table
    from doc_understanding.rag.evidence.containers import containment
    from doc_understanding.rag.indexing.entries import make_entries
    # 3쪽 표 001의 담는 칸(r2c2)은 분석기가 값 셀로 매핑하지만, 원문이 안쪽 표 글자와 같아 근거에서 뺀다.
    box = next(c for c in cells_for_table(kb, _table(kb, "p0003/physical001")) if c["container_duplicate"])
    assert box["lookup_eligible"] is False
    info = containment(kb)
    assert box["cell_id"] in info["duplicates"]
    # 칸 안 연결이 미확정인 칸(KB 5쪽 만기후이자율)은 원문을 남긴다(내용이 사라지지 않게).
    held = next(cid for cid, v in info["containers"].items() if not v["covered"])
    assert held not in info["duplicates"]
    assert not any(set(e.get("cell_ids") or []) & info["duplicates"] for e in make_entries(kb))
    # 안쪽 표 셀은 그대로 검색·근거 대상이다.
    inner = cells_for_table(kb, _table(kb, "p0002/physical002"))
    assert any(c["lookup_eligible"] and c["value_raw"].strip() == "0.1" for c in inner)


def test_review_owner_is_kept_unless_a_person_confirms_or_edits(client):
    http, path = client
    data = load(path)
    for c in data.tables[0].cells:
        c.reviewed, c.reviewer = True, "claude"
    save(data, path)
    table = http.get("/api/mapping/table", params=dict(doc="kb", table="p0006/physical001")).json()
    cells = table["expected"]
    cells[0] = dict(cells[0], reviewer="human")          # 사람이 '맞음 · 확인'
    cells[1] = dict(cells[1], column_path=["바꾼 열"])     # 사람이 고침(주체 표시는 그대로 보냄)
    http.put("/api/mapping/table", json=dict(doc="kb", table="p0006/physical001", cells=cells),
             headers={"x-demo-token": "t"})
    stored = load(path).tables[0].cells
    assert [c.reviewer for c in stored] == ["human", "human"]
    table = http.get("/api/mapping/table", params=dict(doc="kb", table="p0006/physical001")).json()
    http.put("/api/mapping/table", json=dict(doc="kb", table="p0006/physical001", cells=table["expected"]),
             headers={"x-demo-token": "t"})
    assert not load(path).reviewed_by_human or all(c.reviewer == "human" for c in load(path).tables[0].cells)


# --- 리뷰 반영: 좌표 없는 셀, 라벨 인용의 원문 보존, 대체 근거가 없는 칸 ---


def _with_cell(doc, table_suffix, r, c, **update):
    tables = []
    for t in doc.physical_tables:
        if t.table_id.endswith(table_suffix):
            cells = tuple(x.model_copy(update=update) if (x.row_index, x.column_index) == (r, c) else x for x in t.cells)
            t = t.model_copy(update=dict(cells=cells))
        tables.append(t)
    return doc.model_copy(update=dict(physical_tables=tuple(tables)))


def test_unresolved_coordinates_do_not_break_container_context(kb):
    from doc_understanding.rag.evidence.cells import cells_for_table
    from doc_understanding.rag.evidence.containers import containment, parents
    doc = _with_cell(kb, "p0003/physical001", 1, 0, row_index=None, column_index=None)  # 담는 칸 왼쪽 라벨 칸
    info = containment(doc)
    assert parents(doc, _table(doc, "p0003/physical002"), info)  # 오류 없이 담긴 위치를 만든다
    assert cells_for_table(doc, _table(doc, "p0003/physical002"))


def test_label_citations_keep_original_label_cells(kb):
    from doc_understanding.rag.evidence.cells import cells_for_table
    from doc_understanding.rag.generation.narrative import build_catalog
    evidence = [dict(e, evidence_id="E1") for e in cells_for_table(kb, _table(kb, "p0003/physical002"))[:1]]
    cells = {c.cell_id: c for t in kb.physical_tables for c in t.cells}
    labels = [s for s in build_catalog(evidence) if s["kind"] == "parent_text" and s.get("cell_id")]
    assert labels and all(s["text_raw"] == cells[s["cell_id"]].original_text and s["source_references"] for s in labels)
    assert not any(" › " in s["text_raw"] for s in build_catalog(evidence))  # 표시용 연결 경로는 인용 후보가 아니다


def test_container_without_replacement_context_is_not_suppressed(kb, monkeypatch):
    from doc_understanding.rag.evidence import containers
    monkeypatch.setattr(containers, "MAX_CONTEXT_CHARS", 5)  # 가장 짧은 칸 안 문장(8자)보다 작게
    monkeypatch.setattr(containers, "_CACHE", {})
    info = containers.containment(kb)
    assert info["containers"] and not info["duplicates"]  # 칸 안 문장이 투영 한도를 넘으면 원문을 남긴다


def test_routes_serve_and_save_a_named_set_only_from_the_set_folder(client):
    http, path = client
    other = path.parent / "mapping-holdout-x.json"
    data = load(path)
    data.name = "mapping-holdout-x"
    save(data, other)
    assert "mapping-holdout-x" in http.get("/api/mapping").json()["sets"]
    assert http.get("/api/mapping", params=dict(set="mapping-holdout-x")).json()["name"] == "mapping-holdout-x"
    table = http.get("/api/mapping/table", params=dict(doc="kb", table="p0006/physical001", set="mapping-holdout-x")).json()
    cells = [dict(c, reviewed=True, reviewer="human") for c in table["expected"]]
    before = path.read_text(encoding="utf-8")
    http.put("/api/mapping/table", json=dict(doc="kb", table="p0006/physical001", cells=cells, set="mapping-holdout-x"),
             headers={"x-demo-token": "t"})
    assert all(c.reviewed for c in load(other).tables[0].cells) and path.read_text(encoding="utf-8") == before
    for bad in ("../secret", "mapping-missing", "rag-dev-v1"):
        assert http.get("/api/mapping", params=dict(set=bad)).status_code == 404


# --- 실패 장부: 틀리거나 빠진 셀을 쪽·표·셀과 파싱된 경로·기대 경로로 기록한다 ---


def test_failure_records_carry_page_parsed_and_expected_paths(kb, tmp_path):
    from doc_understanding.evaluation import failure_log
    from doc_understanding.evaluation.mapping import failures
    view = table_view(kb, _table(kb, "p0006/physical001"))
    table = ExpectedTable(doc="kb", table="p0006/physical001", patterns=["regular_grid"], cells=[
        expected(view, 1, 1, row=["재예치기간"], col=["다른 열"]),                                   # 틀린 확정
        expected(view, 1, 2, row=cell_at(view, 1, 2)["row_path"], col=cell_at(view, 1, 2)["column_path"]),  # 정상
        expected(view, 0, 0, role="value", row=["x"]),                                           # 값인데 근거 아님
        ExpectedCell(r=1, c=3, text="원문이바뀜", role="value", row_path=["x"]),                    # 좌표 불일치
    ])
    data = MappingSet(name="mapping-x", status="draft", reviewed_by_human=False, documents={"kb": kb.document_id},
                      tables=[table])
    items = failures.mapping_failures(data, document_views({kb.document_id: kb}))
    by = {(f["r"], f["c"]): f for f in items}
    assert {k: f["kind"] for k, f in by.items()} == {(1, 1): "wrong_confirmed", (0, 0): "withheld", (1, 3): "cell_mismatch"}
    wrong = by[1, 1]
    assert wrong["page"] == 6 and wrong["set"] == "mapping-x" and wrong["patterns"] == ["regular_grid"]
    assert wrong["expected_column_path"] == ["다른 열"] and wrong["parsed_column_path"] == ["고정금리형 일 단위 계좌"]
    summary = failures.summarize(items)
    assert summary["total"] == 3 and summary["by_kind"]["wrong_confirmed"] == 1 and summary["tables"] == 1
    path = failure_log.record("mapping", data.name, "t", items, summary, tmp_path / "run", commit="abc",
                              ledger=tmp_path / "ledger.jsonl")
    failure_log.record("mapping", data.name, "t2", [], failures.summarize([]), tmp_path / "run2", ledger=tmp_path / "ledger.jsonl")
    lines = failure_log.read_ledger(tmp_path / "ledger.jsonl")
    assert [x["total"] for x in lines] == [3, 0] and lines[0]["commit"] == "abc" and path.is_file()


def test_intake_flags_missing_text_and_overlapping_cells(kb):
    from doc_understanding.evaluation import intake
    assert intake.document_issues(kb) == []  # 글자 있는 정상 문서
    pages = tuple(p.model_copy(update=dict(text_status="ocr_required")) if p.page_number == 1 else p
                  for p in kb.document_ir.pages)
    tables = []
    for t in kb.physical_tables:
        if t.table_id.endswith("p0005/physical002"):
            t = t.model_copy(update=dict(cells=tuple(c.model_copy(update=dict(original_text="")) for c in t.cells)))
        if t.table_id.endswith("p0003/physical002"):
            first = t.cells[0]
            t = t.model_copy(update=dict(cells=t.cells + (first.model_copy(update=dict(cell_id=first.cell_id + "x")),)))
        tables.append(t)
    doc = kb.model_copy(update=dict(document_ir=kb.document_ir.model_copy(update=dict(pages=pages)),
                                    physical_tables=tuple(tables)))
    kinds = {(i["kind"], i["page"], i["table"]) for i in intake.document_issues(doc, alias="kb")}
    assert kinds == {("OCR_REQUIRED", 1, None), ("TABLE_WITHOUT_TEXT", 5, "p0005/physical002"),
                     ("OVERLAPPING_CELLS", 3, "p0003/physical002")}


def test_failures_route_lists_cells_that_differ(client):
    http, _ = client
    data = http.get("/api/mapping/failures").json()
    assert data["summary"]["by_kind"] == {"wrong_confirmed": 1} and data["failures"][0]["page"] == 6


def test_container_content_mismatch_is_a_recorded_failure(kb):
    from doc_understanding.evaluation.mapping import failures
    from doc_understanding.evaluation.mapping.dataset import content_refs
    views = document_views({kb.document_id: kb})
    outer = views[_table(kb, "p0002/physical000")]
    items = content_refs(cell_at(outer, 0, 1)["container"]["items"])
    table = ExpectedTable(doc="kb", table="p0002/physical000",
                          cells=[expected(outer, 0, 1, role="container", content=list(reversed(items)))])
    data = MappingSet(name="m", status="draft", reviewed_by_human=False, documents={"kb": kb.document_id}, tables=[table])
    found = failures.mapping_failures(data, views)
    assert [f["kind"] for f in found] == ["content_mismatch"]
    assert found[0]["expected_content"] != found[0]["parsed_content"] and len(found[0]["parsed_content"]) == len(items)


def test_routes_show_the_original_page_or_html_for_documents_without_a_web_job(client, tmp_path, kb):
    import fitz

    from doc_understanding.web.mapping_routes import router
    _, path = client
    pdf, html = tmp_path / "source.pdf", tmp_path / "source.html"
    with fitz.open() as document:
        document.new_page()
        document.save(pdf)
    html.write_text("<table><tr><td>원문</td></tr></table><script>x()</script>", encoding="utf8")
    sources = {kb.document_id: pdf}
    app = FastAPI()
    app.include_router(router(lambda request: None, tmp_path / "static", lambda: "t", lambda: {kb.document_id: kb},
                              set_path=path, page_sources=lambda docs: dict(sources), page_cache=tmp_path / "pages"))
    http = TestClient(app)
    table = http.get("/api/mapping/table", params=dict(doc="kb", table="p0006/physical001")).json()
    assert table["page_source"] == "pdf" and table["page_job"] is None and table["source_uri"] is None
    image = http.get("/api/mapping/page", params=dict(doc="kb", number=1))
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
    assert len(list((tmp_path / "pages").rglob("page-1.png"))) == 1 and not list((tmp_path / "pages").rglob("*.part"))
    assert http.get("/api/mapping/page", params=dict(doc="kb", number=1)).content == image.content
    assert http.get("/api/mapping/page", params=dict(doc="kb", number=2)).status_code == 404
    # 테스트셋에 없는 문서 이름이나 파일 경로로는 원본을 읽을 수 없다.
    assert http.get("/api/mapping/page", params=dict(doc=str(pdf)[:40], number=1)).status_code == 404
    assert http.get("/api/mapping/source", params=dict(doc="kb")).status_code == 404
    # HTML 원문은 스크립트·외부 요청을 막는 정책과 함께 내보낸다.
    sources[kb.document_id] = html
    assert http.get("/api/mapping?reload=true").status_code == 200
    table = http.get("/api/mapping/table", params=dict(doc="kb", table="p0006/physical001")).json()
    assert table["page_source"] == "html"
    source = http.get("/api/mapping/source", params=dict(doc="kb"))
    assert source.status_code == 200 and "원문" in source.text
    assert source.headers["content-security-policy"].startswith("sandbox;")
    assert http.get("/api/mapping/page", params=dict(doc="kb", number=1)).status_code == 404


def test_original_files_are_matched_by_content_hash_not_by_name_alone(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace

    from doc_understanding.web import app as web
    (tmp_path / "data/a").mkdir(parents=True)
    (tmp_path / "data/b").mkdir()
    (tmp_path / "data/a/doc.pdf").write_bytes(b"other file with the same name")
    (tmp_path / "data/b/doc.pdf").write_bytes(b"the ingested file")
    document_id = "sha256:" + hashlib.sha256(b"the ingested file").hexdigest()
    named = lambda name: SimpleNamespace(document_ir=SimpleNamespace(source_name=name))  # noqa: E731
    monkeypatch.setattr(web, "ROOT", tmp_path)
    monkeypatch.setattr(web, "DATA", tmp_path / "jobs")
    found = web.mapping_page_sources({document_id: named("doc.pdf"), "sha256:" + "0" * 64: named("doc.pdf"),
                                      "sha256:" + "1" * 64: named(None)})
    assert found == {document_id: tmp_path / "data/b/doc.pdf"}
    # 이름이 달라도 내용이 같으면 찾는다.
    assert web.mapping_page_sources({document_id: named("renamed.pdf")}) == found
