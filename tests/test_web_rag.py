"""웹 RAG의 동의·문서 범위·중복 실행·실패 복구를 검사한다. 실제 외부 호출은 없다."""

from concurrent.futures import Future
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from tests.http_helpers import request as http_request

from doc_understanding.services import web_rag as module
from doc_understanding.services.web_rag import (
    PrepareRequest,
    QuestionRequest,
    WebRagError,
    WebRagService,
    write_json,
)
from doc_understanding.rag.embedding.models import EmbeddingConfig, input_hash
from doc_understanding.web.rag_routes import router

JID = "a" * 32
DIGEST = "document-hash"


class ManualPool:
    def __init__(self):
        self.pending = []

    def submit(self, fn, *args):
        self.pending.append((fn, args))
        return Future()

    def run(self):
        fn, args = self.pending.pop(0)
        fn(*args)

    def shutdown(self, **kwargs):
        pass


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "ThreadPoolExecutor", lambda **kw: ManualPool())
    instance = WebRagService(tmp_path / "documents", tmp_path / "rag")
    monkeypatch.setattr(instance, "document", lambda jid: (SimpleNamespace(), DIGEST))
    monkeypatch.setattr(instance, "manifest", lambda jid, doc: tmp_path / "manifest.json")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-never-sent")
    return instance


def config(**kwargs):
    return EmbeddingConfig(input_usd_per_million=0.02, dimensions=3, **kwargs)


def stored(service):
    value = dict(
        run_id="db-run",
        document_sha256=DIGEST,
        index_version=module.INDEX_VERSION,
        search_entries=2,
    )
    write_json(service.output / JID / "stored.json", value)
    return value


def test_consent_key_and_settings_are_checked_before_enqueue(service, monkeypatch):
    with pytest.raises(WebRagError, match="EXTERNAL_CALL_NOT_ENABLED"):
        service.start(JID, "prepare", PrepareRequest(request_id=uuid4().hex, embedding=config()))
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(WebRagError, match="API_KEY_UNAVAILABLE"):
        service.start(
            JID,
            "prepare",
            PrepareRequest(request_id=uuid4().hex, allow_external=True, embedding=config()),
        )
    assert not service.pool.pending
    # 키가 없어도 로컬 DB 적재 요청은 가능하다.
    task = service.start(JID, "prepare", PrepareRequest(request_id=uuid4().hex, embed=False))
    assert task["status"] == "queued"


def test_idempotent_request_and_global_busy_guard(service):
    request = PrepareRequest(request_id=uuid4().hex, embed=False)
    first = service.start(JID, "prepare", request)
    assert service.start(JID, "prepare", request)["request_id"] == first["request_id"]
    assert len(service.pool.pending) == 1
    with pytest.raises(WebRagError, match="REQUEST_ID_CONFLICT"):
        service.start(JID, "prepare", request.model_copy(update={"embed": True}))
    with pytest.raises(WebRagError, match="RAG_BUSY"):
        service.start("b" * 32, "prepare", PrepareRequest(request_id=uuid4().hex, embed=False))


def test_storage_then_embedding_progress_and_partial_coverage(service, monkeypatch):
    calls = []
    monkeypatch.setattr(module.repository, "store", lambda *a: stored(service))
    monkeypatch.setattr(module.vectors, "migrate", lambda: calls.append("migrate"))

    def embed(rid, client, *, progress):
        calls.append(rid)
        progress(dict(total=2, reused=0, written=1, skipped=[]))
        assert service.state(JID)["prepare"]["written"] == 1
        return dict(
            total=2,
            reused=0,
            written=1,
            skipped=[{"entry_id": "long", "reason": "EMPTY_OR_OVERSIZED_INPUT"}],
            coverage_complete=False,
        )

    monkeypatch.setattr(module, "embed_run", embed)
    service.start(
        JID,
        "prepare",
        PrepareRequest(request_id=uuid4().hex, embedding=config(), allow_external=True),
    )
    service.pool.run()
    state = service.state(JID)
    assert state["prepare"]["status"] == "partial"
    assert state["prepare"]["written"] == 1 and not state["busy"]
    assert calls == ["migrate", "db-run"]


def test_failed_task_preserves_progress_and_redacts_provider_error(service, monkeypatch):
    monkeypatch.setattr(module.repository, "store", lambda *a: stored(service))
    monkeypatch.setattr(module.vectors, "migrate", lambda: None)

    def fail(rid, client, *, progress):
        progress(dict(total=2, reused=0, written=1, skipped=[]))
        raise RuntimeError("secret key and document source")

    monkeypatch.setattr(module, "embed_run", fail)
    service.start(
        JID,
        "prepare",
        PrepareRequest(request_id=uuid4().hex, embedding=config(), allow_external=True),
    )
    service.pool.run()
    task = service.state(JID)["prepare"]
    assert task["status"] == "failed" and task["written"] == 1
    assert task["error"] == "RAG_OPERATION_FAILED"
    assert "secret" not in str(task)
    # 실패 이후 새 요청은 막지 않으며 기존 저장 실행은 재사용한다.
    service.start(JID, "prepare", PrepareRequest(request_id=uuid4().hex, embed=False))


def test_restart_marks_inflight_interrupted_without_replaying(service, monkeypatch):
    req = PrepareRequest(request_id=uuid4().hex, embed=False)
    service.start(JID, "prepare", req)
    restored = WebRagService(service.documents, service.output)
    assert restored.state(JID)["prepare"]["status"] == "interrupted"
    assert not restored.pool.pending
    monkeypatch.setattr(restored, "document", service.document)
    assert restored.start(JID, "prepare", req)["status"] == "interrupted"
    assert not restored.pool.pending


def test_question_requires_matching_prepared_document(service, monkeypatch):
    req = QuestionRequest(request_id=uuid4().hex, question="조건?", mode="lexical")
    with pytest.raises(WebRagError, match="SEARCH_PREPARATION_REQUIRED"):
        service.start(JID, "question", req)
    stored(service)
    monkeypatch.setattr(service, "document", lambda jid: (None, "changed"))
    with pytest.raises(WebRagError, match="SEARCH_PREPARATION_STALE"):
        service.start(JID, "question", req)


def test_lexical_question_scopes_search_and_never_creates_model_client(service, monkeypatch):
    stored(service)
    monkeypatch.delenv("OPENAI_API_KEY")
    hits = [dict(run_id="db-run", payload={"text": "<script>untrusted</script>"})]
    monkeypatch.setattr(
        module.repository,
        "search",
        lambda q, rid, n, run_ids=None: hits if run_ids == ["db-run"] else pytest.fail("wrong scope"),
    )
    monkeypatch.setattr(module, "resolve_evidence", lambda h, q, **kw: [{"evidence_id": "E1"}])
    monkeypatch.setattr(module, "EmbeddingClient", lambda *a, **kw: pytest.fail("no embedding"))
    monkeypatch.setattr(module, "generate_answer", lambda *a, **kw: pytest.fail("no answer"))
    service.start(
        JID, "question", QuestionRequest(request_id=uuid4().hex, question="조건?", mode="lexical")
    )
    service.pool.run()
    task = service.state(JID)["question"]
    assert task["status"] == "completed" and task["hits"] == hits and "result" not in task


def test_answer_passes_explicit_prices_and_retains_abstention(service, monkeypatch):
    stored(service)
    monkeypatch.setattr(module.repository, "search", lambda *a, **k: [])
    monkeypatch.setattr(module, "resolve_evidence", lambda *a, **kw: [])

    def generate(q, evidence, output, **kwargs):
        assert kwargs["allow_external"] and kwargs["verify_sql"]
        assert kwargs["input_usd_per_million"] == 2.0
        assert not output.exists()
        return dict(answer="근거 부족", abstained=True, model_calls=0)

    monkeypatch.setattr(module, "generate_answer", generate)
    req = QuestionRequest(
        request_id=uuid4().hex, question="조건?", mode="lexical", answer=True, allow_external=True
    )
    with pytest.raises(WebRagError, match="EXPLICIT_MODEL_PRICING_REQUIRED"):
        service.start(JID, "question", req)
    service.start(
        JID,
        "question",
        req.model_copy(update={"input_usd_per_million": 2.0, "output_usd_per_million": 8.0}),
    )
    service.pool.run()
    assert service.state(JID)["question"]["result"]["abstained"]


def test_entries_show_exact_input_and_hash_status_with_filters(service, monkeypatch):
    entries = [
        dict(entry_id="e1", text="첫 줄\n둘째 줄\r\n", kind="table_row", page=4),
        dict(entry_id="e2", text="x" * 8000, kind="paragraph", page=1),
    ]
    monkeypatch.setattr(module, "make_entries", lambda doc: [dict(e) for e in entries])
    task = dict(
        job_id=JID,
        request_id=uuid4().hex,
        action="prepare",
        status="ready",
        run_id="db-run",
        document_sha256=DIGEST,
        index_version=module.INDEX_VERSION,
        config={"embedding": config().model_dump()},
    )
    service._save(task)
    monkeypatch.setattr(
        module.vectors, "cached_hashes", lambda *a: {"e1": input_hash(entries[0]["text"])}
    )
    result = service.entries(JID, kind="table_row", page=4)
    assert result["total"] == 2 and result["filtered"] == 1
    assert result["items"][0]["text"] == entries[0]["text"]
    assert result["counts"] == dict(embedded=1, pending=0, excluded=1, unknown=0)


def test_routes_authorize_and_bound_document(service):
    def authorize(request):
        if request.headers.get("x-demo-token") != "test-token":
            raise HTTPException(403)

    jobs = {JID: {"status": "done"}, "b" * 32: {"status": "running"}}
    routes = router(lambda: service, lambda: jobs, authorize, service.output)
    endpoints = {route.path: route.endpoint for route in routes.routes}
    state = endpoints["/api/jobs/{jid}/rag"]
    for jid, code in [("missing", 404), ("b" * 32, 409)]:
        with pytest.raises(HTTPException) as caught:
            state(jid)
        assert caught.value.status_code == code
    prepare = endpoints["/api/jobs/{jid}/rag/prepare"]
    payload = PrepareRequest(request_id=uuid4().hex, embed=False)
    with pytest.raises(HTTPException) as caught:
        prepare(JID, payload, http_request({}))
    assert caught.value.status_code == 403
    assert prepare(JID, payload, http_request({"x-demo-token": "test-token"}))["status"] == "queued"


def test_invalid_job_paths_are_rejected(tmp_path):
    instance = WebRagService(tmp_path, tmp_path / "output")
    try:
        for jid in ("../private", "x" * 32, ""):
            with pytest.raises(WebRagError):
                instance.document(jid)
    finally:
        instance.close()


def test_hybrid_uses_document_scope_and_retains_retrieval_audit(service, monkeypatch):
    stored(service)

    class Retriever:
        def __init__(self, mode, client):
            assert mode == "hybrid" and client.config.dimensions == 3

        def __call__(self, question, run_id, limit, run_ids=None):
            assert run_ids == ["db-run"] and question == "조건?"
            return []

        def report(self):
            return {"retrieval": [{"status": "no_matching_embeddings"}]}

    monkeypatch.setattr(module, "Retriever", Retriever)
    monkeypatch.setattr(module, "resolve_evidence", lambda *a, **kw: [])
    service.start(
        JID,
        "question",
        QuestionRequest(
            request_id=uuid4().hex, question="조건?", embedding=config(), allow_external=True
        ),
    )
    service.pool.run()
    task = service.state(JID)["question"]
    assert task["status"] == "completed"
    assert task["retrieval_audit"]["retrieval"][0]["status"] == "no_matching_embeddings"


def test_embedding_progress_only_advances_after_committed_batches(monkeypatch):
    from doc_understanding.rag.embedding import indexer

    cfg = config(batch_size=1)
    entries = [dict(entry_id="one", text="first"), dict(entry_id="two", text="second")]
    monkeypatch.setattr(indexer.repository, "load_entries", lambda rid: entries)
    monkeypatch.setattr(indexer.repository, "cached_hashes", lambda *a: {})
    monkeypatch.setattr(indexer.repository, "copy_identical", lambda *a: 0)
    saved, progress = [], []

    def save(rid, cfg, items):
        if saved:
            raise RuntimeError("failed second batch")
        saved.extend(items)

    monkeypatch.setattr(indexer.repository, "save_batch", save)
    client = SimpleNamespace(config=cfg, embed=lambda texts: [[1, 0, 0]], audit=[])
    with pytest.raises(RuntimeError):
        indexer.embed_run("rid", client, progress=lambda value: progress.append(value.copy()))
    assert [p["written"] for p in progress] == [0, 1]


def local_config(**kwargs):
    return EmbeddingConfig(
        provider="local", model="bge-m3", dimensions=3, input_usd_per_million=0.0, **kwargs
    )


def test_local_embedding_prepare_needs_no_consent_or_key(service, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    task = service.start(
        JID, "prepare", PrepareRequest(request_id=uuid4().hex, embedding=local_config())
    )
    assert task["status"] == "queued" and task["config"]["embedding"]["provider"] == "local"


def test_local_hybrid_question_is_free_but_answer_still_needs_consent(service, monkeypatch):
    stored(service)
    monkeypatch.delenv("OPENAI_API_KEY")
    req = QuestionRequest(request_id=uuid4().hex, question="조건?", embedding=local_config())
    assert service.start(JID, "question", req)["status"] == "queued"
    service.pool.pending.clear()
    service.active = None
    answer = QuestionRequest(
        request_id=uuid4().hex, question="조건?", embedding=local_config(), answer=True,
        input_usd_per_million=2.0, output_usd_per_million=8.0,
    )
    with pytest.raises(WebRagError, match="EXTERNAL_CALL_NOT_ENABLED"):
        service.start(JID, "question", answer)
    with pytest.raises(WebRagError, match="API_KEY_UNAVAILABLE"):
        service.start(JID, "question", answer.model_copy(update={"allow_external": True}))


def test_prepare_stores_reviewed_document_and_keeps_original(service, monkeypatch, tmp_path):
    from doc_understanding.agents.structure_review import review as review_module

    seen = []
    reviewed = SimpleNamespace(model_dump_json=lambda indent=None: '{"reviewed": true}')
    report = dict(summary=dict(layout_decided=1, layout_ambiguous=0, scope_decided=1, scope_ambiguous=0,
                               usable_values_before=1, usable_values_after=4), applied=[1, 2], rejected=[])
    monkeypatch.setattr(review_module, "review_document", lambda doc: (reviewed, report))
    monkeypatch.setattr(module.repository, "store", lambda manifest, path: seen.append(path) or stored(service))
    service.start(JID, "prepare", PrepareRequest(request_id=uuid4().hex, embed=False))
    service.pool.run()
    task = service.state(JID)["prepare"]
    assert task["status"] == "stored" and task["structure_review"]["status"] == "applied"
    assert task["structure_review"]["usable_values_after"] == 4
    assert seen[0].name == "document.json" and "reviewed" in seen[0].parts
    assert seen[0].read_text(encoding="utf-8") == '{"reviewed": true}'


def test_review_can_be_disabled_and_failure_falls_back_to_original(service, monkeypatch):
    from doc_understanding.agents.structure_review import review as review_module

    seen = []
    monkeypatch.setattr(module.repository, "store", lambda manifest, path: seen.append(path) or stored(service))
    monkeypatch.setattr(review_module, "review_document", lambda doc: pytest.fail("review disabled"))
    service.start(JID, "prepare", PrepareRequest(request_id=uuid4().hex, embed=False, structure_review=False))
    service.pool.run()
    assert "structure_review" not in service.state(JID)["prepare"]

    def broken(doc):
        raise AttributeError("bad document")

    monkeypatch.setattr(review_module, "review_document", broken)
    service.start(JID, "prepare", PrepareRequest(request_id=uuid4().hex, embed=False))
    service.pool.run()
    task = service.state(JID)["prepare"]
    assert task["status"] == "stored" and task["structure_review"] == {"status": "failed", "error": "AttributeError"}
    assert seen[-1] == service._folder(JID) / "document.json"


def test_agent_question_requires_answer_and_records_plan(service, monkeypatch):
    from doc_understanding.rag.agentic import run as agent_run

    stored(service)
    with pytest.raises(WebRagError, match="AGENT_REQUIRES_ANSWER"):
        service.start(JID, "question", QuestionRequest(request_id=uuid4().hex, question="조건?",
                                                       mode="lexical", agent=True))
    seen = {}

    def fake_answer(question, output, **kwargs):
        seen.update(kwargs)
        trace = dict(hits=[dict(entry_id="e1")], tables=[], structured=[dict(cell_id="c1")])
        return dict(plan=dict(requirements=[dict(role="item", text="조건")]), plan_check=dict(fallback=False, call={}),
                    attempts=[dict(trace=trace)], retried=False, result=dict(answer="ok"),
                    evidence=[dict(evidence_id="E1", page=1)], plan_model_calls=1, answer_model_calls=1,
                    plan_estimated_cost_usd=0.0001)

    monkeypatch.setattr(agent_run, "answer", fake_answer)
    monkeypatch.setattr(service, "_trace", lambda: None)
    req = QuestionRequest(request_id=uuid4().hex, question="조건?", mode="lexical", answer=True, agent=True,
                          allow_external=True, input_usd_per_million=2.0, output_usd_per_million=8.0)
    service.start(JID, "question", req)
    service.pool.run()
    task = service.state(JID)["question"]
    assert task["status"] == "completed" and task["result"]["answer"] == "ok"
    assert task["agent"]["structured"] == [{"cell_id": "c1"}] and "call" not in task["agent"]["plan_check"]
    # 검색 범위는 이 문서의 실행 하나로 제한된 검색기로 전달된다.
    calls = []
    monkeypatch.setattr(module.repository, "search", lambda q, rid, n, run_ids=None: calls.append(run_ids) or [])
    seen["retriever"]("q", None, 3)
    assert calls == [["db-run"]] and seen["max_documents"] == 1
    assert seen["plan_policy"].input_usd_per_million == 2.0
    assert seen["answer_mode"] == "selection" and seen["allow_external"] is True


def test_state_reports_whether_questions_can_run_and_default_answer_mode(service, monkeypatch):
    state = service.state(JID)
    assert state["prepared_current"] is False and state["index_version"] == module.INDEX_VERSION
    stored(service)
    assert service.state(JID)["prepared_current"] is True
    assert QuestionRequest(request_id=uuid4().hex, question="q").answer_mode == "selection"
    with pytest.raises(ValueError):
        QuestionRequest(request_id=uuid4().hex, question="q", answer_mode="free")


def test_reviewed_preparation_is_ready_for_questions(service, monkeypatch, tmp_path):
    # 회귀: 검토 후보 문서로 색인하면 stored.json 해시가 검토 문서 해시가 되어 질문이 STALE로 거절됐다.
    from doc_understanding.agents.structure_review import review as review_module

    folder = service._folder(JID)
    folder.mkdir(parents=True)
    (folder / "document.json").write_bytes(b'{"original": true}')
    monkeypatch.setattr(service, "document", lambda jid: (SimpleNamespace(), module.hashlib.sha256(
        (folder / "document.json").read_bytes()).hexdigest()))
    reviewed = SimpleNamespace(model_dump_json=lambda indent=None: '{"reviewed": true}')
    report = dict(summary=dict(usable_values_before=1, usable_values_after=2), applied=[1], rejected=[])
    monkeypatch.setattr(review_module, "review_document", lambda doc: (reviewed, report))
    monkeypatch.setattr(module.repository, "store", lambda manifest, path: dict(
        run_id="db-run", search_entries=2, document_sha256=module.hashlib.sha256(path.read_bytes()).hexdigest()))
    service.start(JID, "prepare", PrepareRequest(request_id=uuid4().hex, embed=False))
    service.pool.run()
    assert service.state(JID)["prepare"]["structure_review"]["status"] == "applied"
    assert service.state(JID)["prepared_current"] is True
    stored_json = module.read_json(service.output / JID / "stored.json")
    assert stored_json["indexed_document_sha256"] != stored_json["document_sha256"]


def test_agent_can_use_a_separate_plan_model(service, monkeypatch):
    from doc_understanding.rag.agentic import run as agent_run

    stored(service)
    seen = {}

    def fake_answer(question, output, **kwargs):
        seen.update(kwargs)
        trace = dict(hits=[], tables=[], structured=[])
        return dict(plan=dict(requirements=[]), plan_check=dict(fallback=False), attempts=[dict(trace=trace)],
                    retried=False, result=dict(answer="ok"), evidence=[], plan_model_calls=1,
                    answer_model_calls=1, plan_estimated_cost_usd=0.0)

    monkeypatch.setattr(agent_run, "answer", fake_answer)
    monkeypatch.setattr(service, "_trace", lambda: None)
    req = QuestionRequest(request_id=uuid4().hex, question="조건?", mode="lexical", answer=True, agent=True,
                          allow_external=True, model="gpt-4.1", input_usd_per_million=2.0,
                          output_usd_per_million=8.0, plan_model="gpt-4.1-mini",
                          plan_input_usd_per_million=0.4, plan_output_usd_per_million=1.6)
    service.start(JID, "question", req)
    service.pool.run()
    assert seen["plan_policy"].model == "gpt-4.1-mini" and seen["plan_policy"].input_usd_per_million == 0.4
    assert seen["model"] == "gpt-4.1" and seen["input_usd_per_million"] == 2.0


def test_review_model_is_passed_only_with_a_full_policy_and_external_consent(service, monkeypatch):
    from doc_understanding.agents.structure_review import review as review_module

    seen = []
    report = dict(summary=dict(usable_values_before=1, usable_values_after=1), applied=[], rejected=[])
    monkeypatch.setattr(review_module, "review_document", lambda doc, **kw: seen.append(kw) or (None, report))
    monkeypatch.setattr(module.repository, "store", lambda manifest, path: stored(service))
    model = dict(structure_review_model="m", structure_review_input_usd_per_million=0.4,
                 structure_review_output_usd_per_million=1.6)
    for extra in (dict(model, allow_external=True), dict(model, allow_external=False),
                  dict(structure_review_model="m", allow_external=True), {}):
        service.start(JID, "prepare", PrepareRequest(request_id=uuid4().hex, embed=False, **extra))
        service.pool.run()
    assert seen[0]["policy"].model == "m" and seen[0]["allow_external"] is True and seen[0]["ledger"].max_calls == 40
    assert seen[1:] == [{}, {}, {}]  # 동의·단가가 없으면 규칙만 쓴다


def test_failed_agent_question_keeps_the_latest_answer_failure_record(service, monkeypatch):
    # 표 전체로 넓혀 다시 답하다 실패하면 실패 기록은 answer-whole 같은 새 폴더에 남는다. 그 기록도 과제에 붙는다.
    import json
    import os
    from doc_understanding.rag.agentic import run as agent_run

    stored(service)

    def failing_answer(question, output, **kwargs):
        for age, name in enumerate(("answer", "answer-whole")):
            (output / name).mkdir(parents=True)
            path = output / name / "failure.json"
            path.write_text(json.dumps(dict(folder=name, reserved_cost_usd=0.01)), encoding="utf8")
            os.utime(path, ns=(1_000_000_000 * (age + 1), 1_000_000_000 * (age + 1)))
        raise RuntimeError("ANSWER_CALL_FAILED_SEE_LOCAL_AUDIT")

    monkeypatch.setattr(agent_run, "answer", failing_answer)
    monkeypatch.setattr(service, "_trace", lambda: None)
    service.start(JID, "question", QuestionRequest(request_id=uuid4().hex, question="조건?", mode="lexical", answer=True,
                                                   agent=True, allow_external=True, input_usd_per_million=2.0,
                                                   output_usd_per_million=8.0))
    service.pool.run()
    task = service.state(JID)["question"]
    assert task["status"] == "failed" and task["answer_failure_audit"]["folder"] == "answer-whole"
