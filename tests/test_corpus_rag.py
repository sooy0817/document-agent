"""코퍼스 RAG: 목록·일괄 적재·코퍼스 질문. DB 조회와 외부 호출은 모의다."""
from uuid import uuid4

import pytest

from doc_understanding.services import corpus_rag
from doc_understanding.services.corpus_rag import CorpusQuestionRequest, CorpusService, IngestRequest
from doc_understanding.services.web_rag import WebRagError, write_json
from tests.test_web_rag import ManualPool  # noqa: F401  (fixture가 사용)
from tests.test_web_rag import service  # noqa: F401  pytest fixture 재사용

A, B, C = "a" * 32, "b" * 32, "c" * 32
CURRENT = corpus_rag.INDEX_VERSION
RUNS = [
    dict(run_id="run-a", document_id="doc-a", index_version=CURRENT, entries=10, pages=2, source_name="a.pdf",
         structure_review=True, created_at="t2"),
    dict(run_id="run-old", document_id="doc-c", index_version="document-rag/0.2", entries=5, pages=1,
         source_name="c.pdf", structure_review=False, created_at="t0"),
]
JOBS = {A: dict(status="done", name="문서 A", pages=2), B: dict(status="done", name="문서 B", pages=3),
        C: dict(status="done", name="문서 C", pages=1), "d" * 32: dict(status="running", name="처리 중")}


class FakeCatalog:
    """문서 목록표 대역: RUNS를 목록표의 현재 실행 줄로 본다."""

    def __init__(self, runs):
        self.runs, self.names = list(runs), {}

    def rows(self):
        return [dict(r, name=self.names.get(r["run_id"]) or r["source_name"], is_current=True) for r in self.runs]

    def set_names(self, names):
        self.names.update({k: v for k, v in names.items() if k and v})

    def lookup(self, run_ids):
        return {r["run_id"]: r for r in self.rows() if r["run_id"] in set(run_ids)}

    def current_for_documents(self, document_ids, index_version):
        return {r["document_id"]: r["run_id"] for r in self.rows()
                if r["document_id"] in set(document_ids) and r["index_version"] == index_version}

    def stats(self, index_version):
        current = [r for r in self.runs if r["index_version"] == index_version]
        return dict(documents=len(current), pages=sum(r["pages"] for r in current), tables=0,
                    entries=sum(r["entries"] for r in current), vectors=0, stale_documents=len(self.runs) - len(current))

    def find(self, index_version, text=None, limit=20, offset=0, stale=False):
        rows = [r for r in self.rows() if (r["index_version"] != index_version) == stale
                and (not text or text.lower() in r["name"].lower())]
        return dict(total=len(rows), limit=limit, offset=offset, documents=rows[offset:offset + limit])


class FakeScope:
    """코퍼스 전체 범위 대역: 현재 색인 버전 문서 수만 안다."""

    def __init__(self, index_version):
        self.index_version = index_version

    def __len__(self):
        return len([r for r in corpus_rag.document_catalog.runs if r["index_version"] == self.index_version])

    def __bool__(self):
        return len(self) > 0


@pytest.fixture
def corpus(service, monkeypatch):  # noqa: F811
    monkeypatch.setattr(corpus_rag, "document_catalog", FakeCatalog(RUNS))
    monkeypatch.setattr(corpus_rag, "CorpusScope", FakeScope)
    monkeypatch.setattr(corpus_rag, "embedding_profiles", lambda ids: [
        dict(run_id="run-a", provider="openai", model="text-embedding-3-small", dimensions=1536,
             profile_id="p", vectors=10)])
    write_json(service.output / A / "stored.json", dict(run_id="run-a", index_version=CURRENT))
    write_json(service.output / C / "stored.json", dict(run_id="run-old", index_version="document-rag/0.2"))
    return CorpusService(service)


def test_catalog_summarizes_the_corpus_and_job_states_without_listing_documents(corpus):
    cat = corpus.catalog(JOBS)
    assert "corpus" not in cat and "stale" not in cat
    assert cat["stats"]["documents"] == 1 and cat["stats"]["stale_documents"] == 1 and cat["stats"]["entries"] == 10
    states = {j["job_id"]: j["state"] for j in cat["jobs"]}
    assert states == {A: "in_corpus", B: "not_ingested", C: "stale"}  # 처리 중인 작업은 제외
    assert next(j for j in cat["jobs"] if j["job_id"] == A)["corpus_run_id"] == "run-a"


def test_documents_are_found_by_name_one_page_at_a_time(corpus):
    corpus.catalog(JOBS)  # 업로드한 파일 이름을 표시 이름으로 적는다
    page = corpus.documents(JOBS, "문서")
    assert page["total"] == 1 and page["documents"][0]["name"] == "문서 A" and page["documents"][0]["job_id"] == A
    assert page["documents"][0]["embeddings"][0]["vectors"] == 10
    assert corpus.documents(JOBS, "없는 이름")["total"] == 0
    assert [d["run_id"] for d in corpus.documents(JOBS, stale=True)["documents"]] == ["run-old"]


def test_ingest_continues_after_a_failed_document(corpus, service, monkeypatch):  # noqa: F811
    monkeypatch.setattr(service, "manifest", lambda jid, doc: "manifest")
    seen = []

    def prepare(item, jid, doc, digest, request, save, clients):
        seen.append(jid)
        if jid == B:
            raise ValueError("DOCUMENT_IR_NOT_IDENTICAL")
        item.update(status="ready", run_id="run-" + jid[0])

    monkeypatch.setattr(service, "_prepare_job", prepare)
    corpus.start("ingest", IngestRequest(request_id=uuid4().hex, job_ids=[A, B], embed=False), JOBS)
    service.pool.run()
    task = corpus.state()["ingest"]
    assert seen == [A, B] and task["status"] == "partial"
    assert [(i["job_id"], i["status"]) for i in task["items"]] == [(A, "ready"), (B, "failed")]
    assert task["items"][1]["error"] == "DOCUMENT_IR_NOT_IDENTICAL" and not service.active


def test_ingest_rejects_unfinished_jobs_and_needs_consent_for_openai(corpus):
    with pytest.raises(WebRagError, match="DOCUMENT_NOT_READY"):
        corpus.start("ingest", IngestRequest(request_id=uuid4().hex, job_ids=["d" * 32], embed=False), JOBS)
    from tests.test_web_rag import config
    with pytest.raises(WebRagError, match="EXTERNAL_CALL_NOT_ENABLED"):
        corpus.start("ingest", IngestRequest(request_id=uuid4().hex, job_ids=[A], embedding=config()), JOBS)


def test_question_searches_only_current_corpus_runs(corpus, service, monkeypatch):  # noqa: F811
    seen = {}

    def question(task, request, run_ids, clients, *, max_documents):
        seen.update(run_ids=run_ids, max_documents=max_documents)
        task.update(hits=[], evidence=[])

    monkeypatch.setattr(service, "_question", question)
    corpus.start("question", CorpusQuestionRequest(request_id=uuid4().hex, question="조건?", mode="lexical"), JOBS)
    service.pool.run()
    task = corpus.state()["question"]
    # 전체 검색: 실행 ID 목록이 아니라 코퍼스 범위를 넘긴다. 작업 기록에도 ID를 나열하지 않는다.
    assert task["status"] == "completed" and isinstance(seen["run_ids"], FakeScope) and seen["max_documents"] == 1
    assert task["run_ids"] is None and task["scope"] == dict(kind="corpus", documents=1, index_version=CURRENT)
    assert task["documents"] == {}


def test_question_can_be_narrowed_to_chosen_documents_and_names_the_ones_in_evidence(corpus, service, monkeypatch):  # noqa: F811
    seen = {}

    def question(task, request, run_ids, clients, *, max_documents):
        seen.update(run_ids=run_ids)
        task.update(hits=[dict(run_id="run-a")], evidence=[dict(run_id="run-a", evidence_id="E1")])

    monkeypatch.setattr(service, "_question", question)
    corpus.catalog(JOBS)
    corpus.start("question", CorpusQuestionRequest(request_id=uuid4().hex, question="조건?", mode="lexical",
                                                   run_ids=["run-a"]), JOBS)
    service.pool.run()
    task = corpus.state()["question"]
    assert seen["run_ids"] == ["run-a"] and task["run_ids"] == ["run-a"] and task["scope"]["kind"] == "selected"
    assert task["documents"] == {"run-a": dict(name="문서 A", job_id=A)}


def test_question_rejects_runs_outside_corpus_and_empty_corpus(corpus, service, monkeypatch):  # noqa: F811
    corpus.start("question", CorpusQuestionRequest(request_id=uuid4().hex, question="q", mode="lexical",
                                                   run_ids=["run-old"]), JOBS)
    service.pool.run()
    assert corpus.state()["question"]["error"] == "RUN_NOT_IN_CORPUS"
    corpus_rag.document_catalog.runs = []
    corpus.start("question", CorpusQuestionRequest(request_id=uuid4().hex, question="q", mode="lexical"), JOBS)
    service.pool.run()
    assert corpus.state()["question"]["error"] == "CORPUS_EMPTY"


def test_same_request_id_is_not_rerun(corpus, service, monkeypatch):  # noqa: F811
    monkeypatch.setattr(service, "_question", lambda *a, **k: None)
    request = CorpusQuestionRequest(request_id=uuid4().hex, question="q", mode="lexical")
    first = corpus.start("question", request, JOBS)
    assert corpus.start("question", request, JOBS)["request_id"] == first["request_id"]
    assert len(service.pool.pending) == 1
    with pytest.raises(WebRagError, match="REQUEST_ID_CONFLICT"):
        corpus.start("question", request.model_copy(update={"question": "다른 질문"}), JOBS)


def test_ask_page_injects_token_and_routes_require_authorization(corpus, service, monkeypatch):  # noqa: F811
    from fastapi import HTTPException
    from tests.http_helpers import request as http_request
    from doc_understanding.web.rag_routes import router
    from pathlib import Path

    static = Path(corpus_rag.__file__).resolve().parents[1] / "web/static"

    def authorize(request):
        if request.headers.get("x-demo-token") != "t0k":
            raise HTTPException(403, "denied")

    api = router(lambda: service, lambda: JOBS, authorize, static, lambda: "t0k")
    page = next(r for r in api.routes if r.path == "/ask").endpoint()
    assert b'window.token="t0k"' in page.body and b"__TOKEN__" not in page.body
    ingest = next(r for r in api.routes if r.path == "/api/rag/corpus/ingest").endpoint
    with pytest.raises(HTTPException):
        ingest(IngestRequest(request_id=uuid4().hex, job_ids=[A], embed=False), http_request({}))
