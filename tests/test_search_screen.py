"""검색·답변 화면: 원문 찾기·좌표 사용 조건·원문 API·문서 처리 상태·의견 기록·실행 단계·작업 줄. DB와 외부 호출은 모의다."""
import hashlib
import json
import threading
from types import SimpleNamespace
from uuid import uuid4

import fitz
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from doc_understanding.services import corpus_rag, feedback, source_view
from doc_understanding.services.corpus_rag import CorpusQuestionRequest, IngestRequest
from doc_understanding.services.web_rag import WebRagError, write_json
from doc_understanding.web import search_routes
from tests.test_corpus_rag import JOBS, A, B, corpus  # noqa: F401  (fixture 재사용)
from tests.test_question_agent import PLAN, POLICY, QUESTION, api_key, kb_runtime, scripted  # noqa: F401
from tests.test_web_rag import ManualPool, service  # noqa: F401

TOKEN = "t" * 20


def pdf_bytes(pages=2, width=300, height=400):
    doc = fitz.open()
    for i in range(pages):
        doc.new_page(width=width, height=height).insert_text((40, 60), f"page {i + 1}")
    data = doc.tobytes()
    doc.close()
    return data


def document_id(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


# ---------- 원본 파일 찾기 ----------

def test_source_files_match_by_content_and_do_not_rescan_every_miss(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a/doc.pdf").write_bytes(b"same name, other content")
    (tmp_path / "a/renamed.pdf").write_bytes(b"the ingested file")
    wanted = document_id(b"the ingested file")
    files = source_view.SourceFiles([tmp_path], rescan_seconds=3600)
    assert files.find([wanted, "sha256:" + "0" * 64], ["doc.pdf"]) == {wanted: tmp_path / "a/renamed.pdf"}
    # 방금 끝까지 훑었으므로, 그 뒤에 생긴 파일은 다시 훑을 때까지 찾지 않는다(없는 문서를 열 때마다 폴더를 읽지 않는다).
    (tmp_path / "a/late.html").write_bytes(b"<p>late</p>")
    assert files.find([document_id(b"<p>late</p>")]) == {}
    files.rescan_seconds = 0
    assert files.find([document_id(b"<p>late</p>")]) == {document_id(b"<p>late</p>"): tmp_path / "a/late.html"}
    # 파일이 바뀌거나 없어지면 예전 결과를 쓰지 않는다.
    (tmp_path / "a/renamed.pdf").write_bytes(b"changed")
    assert files.find([wanted]) == {}


@pytest.mark.parametrize("kind,stored,actual,expected", [
    ("pdf", dict(width=300.0, height=400.0, synthetic=False), (300.0, 400.0), (True, None)),
    ("pdf", dict(width=300.0, height=400.0, synthetic=False), (400.0, 300.0), (False, "PAGE_SIZE_MISMATCH")),
    ("pdf", dict(width=300.0, height=400.0, synthetic=True), (300.0, 400.0), (False, "SYNTHETIC_COORDINATES")),
    ("pdf", None, (300.0, 400.0), (False, "PAGE_NOT_FOUND")),
    ("html", dict(width=1000.0, height=4000.0, synthetic=True), None, (False, "NO_PAGE_COORDINATES")),
    (None, dict(width=300.0, height=400.0, synthetic=False), None, (False, "SOURCE_NOT_AVAILABLE")),
])
def test_positions_are_used_only_when_the_stored_page_matches_the_file(kind, stored, actual, expected):
    assert source_view.positions(kind, stored, actual) == expected


def test_page_is_rendered_once_and_missing_pages_are_reported(tmp_path):
    source = tmp_path / "doc.pdf"
    source.write_bytes(pdf_bytes(2))
    target = tmp_path / "cache/page-2.png"
    assert source_view.render_page(source, 2, target) == target and target.read_bytes().startswith(b"\x89PNG")
    stamp = target.stat().st_mtime_ns
    assert source_view.render_page(source, 2, target) == target and target.stat().st_mtime_ns == stamp
    assert source_view.render_page(source, 3, tmp_path / "cache/page-3.png") is None
    assert [p.name for p in (tmp_path / "cache").iterdir()] == ["page-2.png"]  # 임시 파일을 남기지 않는다
    assert source_view.pdf_page_size(source, 1) == (300.0, 400.0) and source_view.pdf_page_size(source, 9) is None


# ---------- 원문 API ----------

class Files:
    def __init__(self, found):
        self.found = found

    def find(self, document_ids, names=()):
        return {d: self.found[d] for d in document_ids if d in self.found}


@pytest.fixture
def screen(tmp_path, monkeypatch):
    pdf, html = pdf_bytes(2), "<html><body><p>원문 문단</p><script>alert(1)</script></body></html>".encode("utf8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.pdf").write_bytes(pdf)
    (tmp_path / "src/b.html").write_bytes(html)
    rows = {
        "run-pdf": dict(run_id="run-pdf", document_id=document_id(pdf), name="문서 A", source_name="a.pdf", pages=2,
                        source_media_type="application/pdf", index_version=search_routes.INDEX_VERSION, is_current=True),
        "run-html": dict(run_id="run-html", document_id=document_id(html), name="문서 B", source_name="b.html", pages=1,
                         source_media_type="text/html", index_version=search_routes.INDEX_VERSION, is_current=True),
        "run-lost": dict(run_id="run-lost", document_id="sha256:" + "9" * 64, name="문서 C", source_name="c.pdf", pages=3,
                         source_media_type="application/pdf", index_version="document-rag/0.1", is_current=False),
    }
    monkeypatch.setattr(search_routes.document_catalog, "lookup", lambda ids: {i: rows[i] for i in ids if i in rows})
    stored = {"run-pdf": dict(width=300.0, height=400.0, synthetic=False),
              "run-html": dict(width=1000.0, height=4000.0, synthetic=True),
              "run-lost": dict(width=300.0, height=400.0, synthetic=False)}
    monkeypatch.setattr(source_view, "stored_page", lambda run, n: stored[run] if n <= rows[run]["pages"] else None)
    web = SimpleNamespace(output=tmp_path / "rag", lock=threading.RLock())

    def authorize(request):
        if request.headers.get("x-demo-token") != TOKEN:
            raise HTTPException(403, "token")

    app = FastAPI()
    files = Files({rows["run-pdf"]["document_id"]: tmp_path / "src/a.pdf",
                   rows["run-html"]["document_id"]: tmp_path / "src/b.html"})
    app.include_router(search_routes.router(lambda: web, lambda: dict(JOBS), authorize, tmp_path, lambda: TOKEN, files,
                                            page_cache=tmp_path / "pages", feedback_folder=tmp_path / "feedback",
                                            feedback_ledger=tmp_path / "ledger.jsonl"))
    # 요청마다 이벤트 루프 연결을 새로 만들지 않도록 한 번 열어 둔다.
    with TestClient(app) as http:
        yield SimpleNamespace(http=http, web=web, stored=stored, tmp=tmp_path)


def test_pdf_citation_opens_the_page_image_and_allows_highlighting(screen):
    info = screen.http.get("/api/search/source", params=dict(run_id="run-pdf", page=2)).json()
    assert info["kind"] == "pdf" and info["available"] and info["positions"] is True and info["positions_reason"] is None
    assert (info["name"], info["pages"], info["width"], info["height"], info["searchable"]) == ("문서 A", 2, 300.0, 400.0, True)
    image = screen.http.get(info["image"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert screen.http.get("/api/search/source/page", params=dict(run_id="run-pdf", number=3)).status_code == 404
    # 문서에 없는 쪽: 원문은 있지만 그 쪽이 없다고 알린다.
    missing = screen.http.get("/api/search/source", params=dict(run_id="run-pdf", page=3)).json()
    assert missing["positions"] is False and missing["positions_reason"] == "PAGE_NOT_FOUND" and missing["image"] is None


def test_estimated_positions_are_never_presented_as_exact(screen):
    # 저장된 쪽 크기가 지금 파일과 다르면 쪽은 보여 주되 위치는 강조하지 않는다.
    screen.stored["run-pdf"] = dict(width=400.0, height=300.0, synthetic=False)
    info = screen.http.get("/api/search/source", params=dict(run_id="run-pdf", page=1)).json()
    assert info["image"] and info["positions"] is False and info["positions_reason"] == "PAGE_SIZE_MISMATCH"


def test_html_source_is_shown_sandboxed_without_page_positions(screen):
    info = screen.http.get("/api/search/source", params=dict(run_id="run-html", page=1)).json()
    assert info["kind"] == "html" and info["image"] is None and info["positions"] is False
    assert info["positions_reason"] == "NO_PAGE_COORDINATES"
    page = screen.http.get(info["html"])
    assert page.status_code == 200 and "원문 문단" in page.text
    assert page.headers["content-security-policy"].startswith("sandbox;")
    assert screen.http.get("/api/search/source/page", params=dict(run_id="run-html", number=1)).status_code == 404
    assert screen.http.get("/api/search/source/html", params=dict(run_id="run-pdf")).status_code == 404


def test_missing_source_file_is_reported_as_missing(screen):
    info = screen.http.get("/api/search/source", params=dict(run_id="run-lost", page=1)).json()
    assert info["available"] is False and info["kind"] is None and info["image"] is None and info["html"] is None
    assert info["positions"] is False and info["positions_reason"] == "SOURCE_NOT_AVAILABLE" and info["searchable"] is False
    assert screen.http.get("/api/search/source/page", params=dict(run_id="run-lost", number=1)).status_code == 404
    assert screen.http.get("/api/search/source", params=dict(run_id="nope", page=1)).status_code == 404
    # 실행 ID 자리에 경로를 넣을 수 없다.
    assert screen.http.get("/api/search/source", params=dict(run_id="../../x", page=1)).status_code == 422


def test_table_and_context_lookups_return_404_when_not_stored(screen, monkeypatch):
    monkeypatch.setattr(source_view, "table_grid", lambda run, table: None if table == "none" else dict(table_id=table, cells=[]))
    monkeypatch.setattr(source_view, "neighbours", lambda run, content: None if content == "none" else [dict(text="x", cited=True)])
    assert screen.http.get("/api/search/table", params=dict(run_id="run-pdf", table_id="none")).status_code == 404
    assert screen.http.get("/api/search/table", params=dict(run_id="run-pdf", table_id="doc/p0001/physical000")).json()["cells"] == []
    assert screen.http.get("/api/search/context", params=dict(run_id="run-pdf", content_id="none")).status_code == 404
    assert screen.http.get("/api/search/context", params=dict(run_id="run-pdf", content_id="c1")).json()["items"][0]["cited"]


def test_table_grid_joins_physical_cells_with_stored_paths(monkeypatch):
    physical = dict(page_number=3, cells=[
        dict(cell_id="c0", row_index=0, column_index=0, rowspan=None, colspan=None, original_text="구분", source=dict(bbox=dict(x0=1, y0=2, x1=3, y1=4))),
        dict(cell_id="c1", row_index=1, column_index=1, rowspan=2, colspan=1, original_text="806", source=dict(bbox=dict(x0=5, y0=6, x1=7, y1=8))),
        dict(cell_id="floating", row_index=None, column_index=None, original_text="x"),
    ])
    item = dict(scope=["[확정급여형]"], nested_context=dict(parent_table_id="t", parent_cell_id="p", path=[dict(label="만기후")]),
                cells=[dict(cell_id="c1", row_path=["영업이익"], column_path=["2025"], lookup_eligible=False)])

    def query(statement, params):
        if "physical_tables" in statement:
            return [dict(physical=physical, roles=[dict(cell_id="c0", role="corner"), dict(cell_id="c1", role="value")])]
        return [dict(payload=item)]

    monkeypatch.setattr(source_view, "query_params", query)
    grid = source_view.table_grid("run", "table")
    assert (grid["page"], grid["rows"], grid["columns"], grid["scope"]) == (3, 3, 2, ["[확정급여형]"])
    assert grid["nested"] == dict(parent_table_id="t", parent_cell_id="p", path=["만기후"])
    label, value = grid["cells"]
    assert (label["role"], label["row_path"], label["eligible"], label["rs"]) == ("corner", None, False, 1)
    # 미확정 경로는 확정된 것처럼 표시하지 않는다(eligible=False 그대로).
    assert (value["text"], value["rs"], value["row_path"], value["column_path"], value["eligible"]) == ("806", 2, ["영업이익"], ["2025"], False)
    monkeypatch.setattr(source_view, "query_params", lambda statement, params: [dict(physical=None, roles=None)])
    assert source_view.table_grid("run", "table") is None


# ---------- 문서 처리 상태 ----------

def test_upload_states_separate_parsing_from_search_readiness(screen, monkeypatch):
    ids = [c * 32 for c in "abcdef"]
    jobs = {ids[0]: dict(status="running", name="분석 중"), ids[1]: dict(status="done", name="분석만"),
            ids[2]: dict(status="done", name="적재 중"), ids[3]: dict(status="done", name="임베딩 덜 됨"),
            ids[4]: dict(status="done", name="준비됨"), ids[5]: dict(status="error", name="실패", error="분석을 완료하지 못했습니다.")}

    class Fake:
        def __init__(self, web):
            pass

        def catalog(self, known):
            states = {ids[1]: ("not_ingested", None), ids[2]: ("not_ingested", None), ids[3]: ("in_corpus", "run-3"),
                      ids[4]: ("in_corpus", "run-4")}
            return dict(jobs=[dict(job_id=j, state=states[j][0], corpus_run_id=states[j][1]) for j in known if j in states])

        def state(self):
            return dict(busy_ingest=True, ingest=dict(status="running", config=dict(job_ids=[ids[2]]),
                                    items=[dict(job_id=ids[2], status="running", stage="embedding", total=10, embedded=4)]))

    monkeypatch.setattr(corpus_rag, "CorpusService", Fake)
    openai = dict(provider="openai", model="text-embedding-3-small", dimensions=1536)
    # 다른 모델의 벡터가 다 있어도 이 화면이 쓰는 임베딩이 덜 됐으면 '검색 가능'이 아니다.
    monkeypatch.setattr(corpus_rag, "embedding_profiles", lambda runs: [
        dict(run_id="run-3", vectors=3, **openai), dict(run_id="run-3", vectors=8, provider="local", model="bge-m3", dimensions=1024),
        dict(run_id="run-4", vectors=8, **openai)])
    monkeypatch.setattr(search_routes.document_catalog, "lookup", lambda runs: {"run-3": dict(entries=8), "run-4": dict(entries=8)})
    app = FastAPI()
    app.include_router(search_routes.router(lambda: None, lambda: jobs, lambda r: None, screen.tmp, lambda: TOKEN, Files({}),
                                            page_cache=screen.tmp / "p", feedback_folder=screen.tmp / "f"))
    with TestClient(app) as http:
        found = http.get("/api/search/uploads", params=dict(ids=",".join(ids + ["0" * 32, "../x"]))).json()
    by = {i["job_id"]: i for i in found["items"]}
    assert [by[i]["search"] for i in ids] == ["waiting", "not_ingested", "ingesting", "keyword_only", "ready", "waiting"]
    assert by[ids[2]]["stage"] == "embedding" and by[ids[2]]["progress"] == dict(total=10, embedded=4)
    # 파싱 완료와 검색 준비 완료는 다른 상태다: 색인만 되고 임베딩이 덜 된 문서는 '검색 가능'으로 표시하지 않는다.
    assert by[ids[3]]["parsing"] == "done" and by[ids[3]]["vectors"] == 3 and by[ids[4]]["vectors"] == 8
    assert by[ids[5]]["parsing"] == "error" and by[ids[5]]["parsing_error"]
    assert by["0" * 32]["parsing"] == "unknown" and "../x" not in by and found["busy_ingest"] is True


def test_document_level_preparation_also_reports_the_ingest_lane_as_busy(screen, monkeypatch):
    class Fake:
        def __init__(self, web):
            pass

        def catalog(self, known):
            return dict(jobs=[dict(job_id=A, state="not_ingested", corpus_run_id=None)])

        def state(self):
            return dict(busy_ingest=True, ingest=None)  # 코퍼스 적재는 없고 문서별 검색 준비가 줄을 쓰는 중

    monkeypatch.setattr(corpus_rag, "CorpusService", Fake)
    monkeypatch.setattr(corpus_rag, "embedding_profiles", lambda runs: [])
    found = screen.http.get("/api/search/uploads", params=dict(ids=A)).json()
    assert found["busy_ingest"] is True and found["items"][0]["search"] == "not_ingested"


# ---------- 의견 기록 ----------

def answered_task(web, status="completed"):
    rid = uuid4().hex
    task = dict(job_id="corpus", request_id=rid, action="question", status=status, index_version="v", scope=dict(kind="corpus"),
                config=dict(question="영업이익은?"),
                evidence=[dict(evidence_id="E1", run_id="r", document_id="d", source_name="a.pdf", page=3, table_id="t", cell_id="c",
                               value_raw="806", row_path=["영업이익"], column_path=["2025"], lookup_eligible=True),
                          dict(evidence_id="E2", run_id="r", document_id="d", page=1, content_id="x", value_raw="본문")],
                result=dict(answer_text="806입니다.", answer_text_source="model",
                            selection=dict(evidence_ids=["E1"], values_raw=["806"], abstained=False)))
    write_json(web.output / "corpus/tasks" / (rid + ".json"), task)
    return rid


def test_feedback_is_stored_as_an_unreviewed_opinion_linked_to_the_run(screen):
    rid = answered_task(screen.web)
    body = dict(request_id=rid, kinds=["wrong_citation", "answer_error"], comment=" 단위가 틀림 ", evidence_ids=["E1"])
    assert screen.http.post("/api/search/feedback", json=body).status_code == 403
    saved = screen.http.post("/api/search/feedback", json=body, headers={"X-Demo-Token": TOKEN})
    assert saved.status_code == 201 and saved.json()["review"]["status"] == "unreviewed"
    line = json.loads((screen.tmp / "ledger.jsonl").read_text(encoding="utf8").splitlines()[-1])
    assert (line["kind"], line["set"], line["label"], line["total"]) == ("feedback", "user-screen", rid, 1)
    item = json.loads((screen.tmp / "feedback" / saved.json()["feedback_id"] / "failures.json").read_text(encoding="utf8"))["failures"][0]
    # 질문·답변·실행·인용 근거는 서버의 실행 기록에서 옮긴다. 원래 의견과 검토 결과는 따로 둔다.
    assert item["question"] == "영업이익은?" and item["answer"]["text"] == "806입니다." and item["run"]["request_id"] == rid
    assert item["opinion"] == dict(comment="단위가 틀림", evidence_ids=["E1"])
    assert item["review"] == dict(status="unreviewed", reviewed_by=None, reviewed_at=None, conclusion=None)
    assert [e["cell_id"] for e in item["cited_evidence"]] == ["c"] and [e["evidence_id"] for e in item["flagged_evidence"]] == ["E1"]
    listed = screen.http.get("/api/search/feedback", params=dict(request_id=rid)).json()["items"]
    assert [(i["labels"], i["comment"], i["review"]["status"]) for i in listed] == [(["잘못된 인용", "답변 오류"], "단위가 틀림", "unreviewed")]
    # 의견 기록은 의견 폴더와 장부에만 쓴다(평가셋·색인·문서를 건드리지 않는다).
    written = sorted(p.relative_to(screen.tmp).parts[0] for p in screen.tmp.rglob("*") if p.is_file())
    assert set(written) == {"feedback", "ledger.jsonl", "rag", "src"}


def test_feedback_rejects_unknown_runs_unfinished_answers_and_foreign_evidence(screen):
    headers = {"X-Demo-Token": TOKEN}
    post = lambda body: screen.http.post("/api/search/feedback", json=body, headers=headers).status_code  # noqa: E731
    assert post(dict(request_id=uuid4().hex, kinds=["answer_error"])) == 404
    assert post(dict(request_id=answered_task(screen.web, "running"), kinds=["answer_error"])) == 409
    rid = answered_task(screen.web)
    assert post(dict(request_id=rid, kinds=["answer_error"], evidence_ids=["E9"])) == 422
    assert post(dict(request_id=rid, kinds=["other"])) == 422 and post(dict(request_id=rid, kinds=[])) == 422
    assert not (screen.tmp / "ledger.jsonl").exists()
    # 실패한 질문에도 의견을 남길 수 있다(답이 없어도 기록은 연결된다).
    failed = answered_task(screen.web, "failed")
    assert post(dict(request_id=failed, kinds=["answer_error"])) == 201
    assert feedback.listed(failed, screen.tmp / "ledger.jsonl")[0]["run"]["status"] == "failed"


# ---------- 실행 단계와 작업 줄 ----------

def test_agent_reports_only_the_stages_it_runs(tmp_path, api_key, kb_runtime):  # noqa: F811
    from doc_understanding.rag.agentic.run import answer
    _, retriever, _ = kb_runtime
    seen = []
    supported = dict(answer="ok", model_calls=1, assessment_validation=dict(
        passed=True, assessment=dict(requirements=[dict(question_quote="재예치기간", status="supported")])))
    report = answer(QUESTION, tmp_path / "plain", run_id="run-1", retriever=retriever, plan_policy=POLICY, allow_external=True,
                    plan_transport=scripted(PLAN), generate=lambda q, e, o, **k: dict(supported), progress=seen.append)
    # 리랭커가 없으면 근거 선별 단계는 알리지 않는다. 단계 보고는 결과를 바꾸지 않는다.
    assert seen == ["planning", "searching", "evidence", "answering"] and not report["retried"]

    class Keep:
        def rerank(self, question, plan, evidence, limit, output):
            return evidence[:limit], dict(status="ok")

    seen.clear()
    unsupported = dict(answer="보류", model_calls=1, assessment_validation=dict(
        passed=False, assessment=dict(requirements=[dict(question_quote="확정급여형", status="unsupported")])))
    answer(QUESTION, tmp_path / "retry", run_id="run-1", retriever=retriever, plan_policy=POLICY, allow_external=True,
           plan_transport=scripted(PLAN), generate=lambda q, e, o, **k: dict(unsupported), reranker=Keep(), progress=seen.append)
    assert seen == ["planning", "searching", "evidence", "reranking", "answering",
                    "searching", "evidence", "reranking", "answering"]
    # 근거가 없으면 답변 단계에 들어가지 않는다.
    seen.clear()
    empty = answer(QUESTION, tmp_path / "empty", run_id="run-1", retriever=lambda q, rid, n: [], generate=None, progress=seen.append)
    assert seen == ["planning", "searching", "evidence"] and empty["result"]["abstained"] and not empty["evidence"]


def test_questions_run_while_documents_are_being_ingested(corpus, service, monkeypatch):  # noqa: F811
    monkeypatch.setattr(service, "manifest", lambda jid, doc: "manifest")
    monkeypatch.setattr(service, "_question", lambda task, request, scope, clients, *, max_documents: task.update(hits=[], evidence=[]))
    ingest = corpus.start("ingest", IngestRequest(request_id=uuid4().hex, job_ids=[A], embed=False), JOBS)
    assert corpus.state()["busy_ingest"] and not corpus.state()["busy_question"]
    # 적재가 끝나지 않았어도 질문을 받는다. 같은 줄의 두 번째 작업만 거절한다.
    asked = corpus.start("question", CorpusQuestionRequest(request_id=uuid4().hex, question="조건?", mode="lexical"), JOBS)
    with pytest.raises(WebRagError, match="RAG_BUSY"):
        corpus.start("question", CorpusQuestionRequest(request_id=uuid4().hex, question="또?", mode="lexical"), JOBS)
    with pytest.raises(WebRagError, match="RAG_BUSY"):
        corpus.start("ingest", IngestRequest(request_id=uuid4().hex, job_ids=[B], embed=False), JOBS)
    assert service.lanes == dict(ingest=ingest["request_id"], question=asked["request_id"])
    fn, args = service.pool.pending.pop(1)  # 질문이 적재보다 먼저 끝난다.
    fn(*args)
    done = corpus.task(asked["request_id"])
    assert done["status"] == "completed" and service.lanes == dict(ingest=ingest["request_id"])
    assert corpus.state()["busy"] and corpus.state()["busy_ingest"] and not corpus.state()["busy_question"]
    for bad in ("nope", "../" + "a" * 29, uuid4().hex):
        with pytest.raises(WebRagError, match="TASK_NOT_FOUND"):
            corpus.task(bad)


# ---------- 화면 파일(폴더·모듈) ----------

def test_screen_is_served_from_its_folder_and_every_referenced_module_exists(tmp_path):
    import re
    from pathlib import Path

    static = Path(search_routes.__file__).resolve().parent / "static"
    app = FastAPI()
    app.include_router(search_routes.router(lambda: None, lambda: {}, lambda r: None, static, lambda: TOKEN, Files({}),
                                            page_cache=tmp_path / "p", feedback_folder=tmp_path / "f"))
    with TestClient(app) as http:
        page = http.get("/search")
        assert page.status_code == 200 and f'window.token="{TOKEN}"' in page.text
        links = re.findall(r'(?:href|src)="(/search/assets/[^"?]+)', page.text)
        assert len(links) >= 9 and links[-1].endswith("/js/main.js")
        # 마지막 스타일은 화면 폭 대응이다(앞 파일의 배치를 덮어써야 하므로 순서가 중요하다).
        assert [x for x in links if x.endswith(".css")][-1].endswith("/css/responsive.css")
        for link in links:
            assert http.get(link).status_code == 200, link
        # 모듈이 서로 불러오는 파일도 모두 있어야 한다.
        seen, todo = set(), ["main.js"]
        while todo:
            name = todo.pop()
            if name in seen:
                continue
            seen.add(name)
            got = http.get("/search/assets/js/" + name)
            assert got.status_code == 200 and got.headers["content-type"].startswith("application/javascript"), name
            todo += re.findall(r"from '\./([a-z]+\.js)'", got.text)
        assert {"main.js", "ask.js", "answer.js", "viewer.js", "documents.js", "reader.js", "uploads.js"} <= seen
        assert seen == {p.name for p in (static / "search/js").glob("*.js")}  # 쓰이지 않는 모듈이 없다
        # 설치본에도 화면 폴더가 들어가야 한다(패키지 데이터 목록).
        package = (Path(search_routes.__file__).resolve().parents[3] / "pyproject.toml").read_text(encoding="utf8")
        assert all(x in package for x in ("static/search/*.html", "static/search/css/*.css", "static/search/js/*.js"))
        for bad in ("/search/assets/js/..%2f..%2fask.js", "/search/assets/js/main.py", "/search/assets/other/main.js",
                    "/search/assets/css/nope.css", "/search/assets/js/Main.js"):
            assert http.get(bad).status_code == 404, bad
