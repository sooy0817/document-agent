"""키워드·벡터 결합과 원문 연결 회귀. 모의 벡터는 의미 검색 정확도의 증거가 아니다."""

import json
from types import SimpleNamespace

import pytest

from doc_understanding.rag.embedding.client import EmbeddingClient
from doc_understanding.rag.retrieval import dense, runtime
from doc_understanding.rag.retrieval.hybrid import fuse
from tests.test_rag_embeddings import config, response


def hit(entry_id, score=1, run_id="run"):
    return dict(run_id=run_id, payload={"entry_id": entry_id}, score=score)


def test_rrf_combines_ranks_without_comparing_raw_score_scales():
    lexical = [hit("a", 999), hit("b", 100)]
    vectors = [hit("b", 0.9), hit("c", 0.8)]
    result = fuse(lexical, vectors)
    assert [h["payload"]["entry_id"] for h in result] == ["b", "a", "c"]
    assert result[0]["retrieval"]["ranks"] == {"lexical": 2, "dense": 1}
    assert result[0]["score"] == pytest.approx(1 / 62 + 1 / 61)
    assert lexical[0]["score"] == 999


def test_duplicates_do_not_vote_twice_and_documents_stay_distinct():
    result = fuse([hit("a"), hit("a"), hit("b")], [hit("a", run_id="other")])
    assert len(result) == 3
    assert next(h for h in result if h["payload"]["entry_id"] == "b")["score"] == 1 / 62
    assert result == fuse([hit("a"), hit("a"), hit("b")], [hit("a", run_id="other")])


def test_conflicting_snapshot_is_rejected():
    changed = hit("a")
    changed["payload"]["text"] = "changed"
    with pytest.raises(ValueError, match="SEARCH_SNAPSHOT_CHANGED"):
        fuse([hit("a")], [changed])


def test_dense_sql_keeps_scope_profile_dimensions_current_input_and_bound(monkeypatch):
    statements = []
    monkeypatch.setattr(dense, "serial_query", lambda sql: statements.append(sql) or [])
    assert dense.search_vector([1, 0, 0], config(), []) == []
    assert statements == []
    dense.search_vector([1, 0, 0], config(), ["run'1"], limit=999)
    sql = statements[-1]
    for part in [
        "run''1",
        config().profile_id,
        "v.dimensions=3",
        "v.input_text=e.search_text",
        "v.index_version=s.index_version",
        "NOT IN ('FAILED','RUNNING')",
        "LIMIT 30",
        "<=>",
    ]:
        assert part in sql


def test_scope_uses_latest_ready_document_once_and_handles_explicit_empty(monkeypatch):
    statements = []
    monkeypatch.setattr(dense, "query", lambda sql: statements.append(sql) or [{"run_id": "r"}])
    assert dense.scope_runs(run_ids=[]) == [] and not statements
    assert dense.scope_runs() == ["r"]
    assert "DISTINCT ON (s.document_id)" in statements[-1]
    dense.scope_runs("ignored", run_ids=["one", "two"])
    assert "ignored" not in statements[-1] and "'one','two'" in statements[-1]


def setup_retriever(monkeypatch, *, mode="hybrid", total=3, embedded=2):
    client = EmbeddingClient(config(), allow_external=True, transport=response)
    retriever = runtime.Retriever(mode, client)
    monkeypatch.setattr(dense, "scope_runs", lambda *a, **k: ["r"])
    monkeypatch.setattr(dense, "coverage", lambda *a: dict(total=total, embedded=embedded))
    monkeypatch.setattr(runtime, "lexical_search", lambda *a, **k: [hit("a")])
    monkeypatch.setattr(dense, "search_vector", lambda *a: [hit("b")])
    return retriever


def test_hybrid_records_partial_coverage_and_query_cost(monkeypatch):
    retriever = setup_retriever(monkeypatch)
    result = retriever("question")
    assert {h["payload"]["entry_id"] for h in result} == {"a", "b"}
    audit = retriever.report()
    assert audit["retrieval"][0]["coverage"] == dict(total=3, embedded=2)
    assert len(audit["embedding_calls"]) == 1 and audit["estimated_embedding_cost_usd"] > 0


def test_no_vectors_is_explicit_keyword_only_without_api_call(monkeypatch):
    retriever = setup_retriever(monkeypatch, embedded=0)
    hits = retriever("question")
    assert hits[0]["retrieval"]["method"] == "lexical_only_no_embeddings"
    assert retriever.client.audit == []
    assert retriever.audit[0]["status"] == "no_matching_embeddings"


def test_dense_mode_never_returns_keyword_results_as_vectors(monkeypatch):
    retriever = setup_retriever(monkeypatch, mode="dense", embedded=0)
    monkeypatch.setattr(runtime, "lexical_search", lambda *a, **k: pytest.fail("keyword call"))
    with pytest.raises(ValueError, match="NO_MATCHING_EMBEDDINGS"):
        retriever("question")


def test_external_failure_is_recorded_without_silent_fallback(monkeypatch):
    retriever = setup_retriever(monkeypatch)
    retriever.client.allow_external = False
    with pytest.raises(ValueError, match="EXTERNAL_CALL_NOT_ENABLED"):
        retriever("question")
    assert retriever.audit[0]["status"] == "failed"


def test_hybrid_source_candidates_feed_existing_evidence_resolver(monkeypatch):
    from tests.test_rag_text_chunks import body_document
    from doc_understanding.rag.indexing.entries import make_entries
    from doc_understanding.rag import service

    doc = body_document(["Disconnect power first.", "Do not work while wet."])
    before = doc.model_dump_json()
    entry = make_entries(doc)[0]
    retriever = setup_retriever(monkeypatch)
    monkeypatch.setattr(runtime, "lexical_search", lambda *a, **k: [])
    monkeypatch.setattr(
        dense, "search_vector", lambda *a: [dict(run_id="r", payload=entry, score=0.8)]
    )
    monkeypatch.setattr(service.repository, "evidence_source", lambda _: doc)
    hits = retriever("safe maintenance")
    evidence = service.resolve_evidence(hits, "safe maintenance")
    assert [e["value_raw"] for e in evidence] == [c.text_raw for c in doc.contents]
    assert before == doc.model_dump_json()


def test_lexical_cli_does_not_construct_embedding_client(monkeypatch, tmp_path):
    from doc_understanding.rag import __main__ as cli, repository
    from doc_understanding.rag.embedding import options
    import sys

    monkeypatch.setattr(
        sys, "argv", ["rag", "search", "--question", "q", "--output", str(tmp_path / "hits.json")]
    )
    monkeypatch.setattr(options, "client_from_args", lambda _: pytest.fail("embedding client"))
    monkeypatch.setattr(repository, "search", lambda *a: [hit("a")])
    cli.main()
    assert json.loads((tmp_path / "hits.json").read_text())[0]["payload"]["entry_id"] == "a"


def test_hybrid_search_cli_writes_audit(monkeypatch, tmp_path):
    from doc_understanding.rag import __main__ as cli
    from doc_understanding.rag.embedding import options
    import sys

    retriever = setup_retriever(monkeypatch)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rag",
            "search",
            "--question",
            "q",
            "--retrieval-mode",
            "hybrid",
            "--output",
            str(tmp_path / "hits.json"),
        ],
    )
    monkeypatch.setattr(options, "retriever_from_args", lambda _: retriever)
    cli.main()
    audit = json.loads((tmp_path / "hits.retrieval-audit.json").read_text())
    assert audit["retrieval"][0]["mode"] == "hybrid"


def test_invalid_options_are_not_hidden(monkeypatch):
    from doc_understanding.rag.embedding.options import client_from_args

    with pytest.raises(ValueError, match="EXPLICIT_EMBEDDING_PRICE_REQUIRED"):
        client_from_args(SimpleNamespace(embedding_input_usd_per_million=None))


def test_ask_source_failure_keeps_embedding_usage_audit(monkeypatch, tmp_path):
    from doc_understanding.rag import __main__ as cli, service
    from doc_understanding.rag.embedding import options
    import sys

    retriever = setup_retriever(monkeypatch)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rag",
            "ask",
            "--question",
            "q",
            "--retrieval-mode",
            "hybrid",
            "--output",
            str(tmp_path / "answer"),
        ],
    )
    monkeypatch.setattr(options, "retriever_from_args", lambda _: retriever)

    def fail(*args, **kwargs):
        raise ValueError("SOURCE_MISMATCH")

    monkeypatch.setattr(service, "resolve_evidence", fail)
    with pytest.raises(ValueError, match="SOURCE_MISMATCH"):
        cli.main()
    audit = json.loads((tmp_path / "answer/retrieval-audit.json").read_text())
    assert len(audit["embedding_calls"]) == 1



def test_vector_search_runs_without_parallel_workers_and_reads_only_the_json_rows(monkeypatch):
    # 병렬 작업자가 컨테이너 공유 메모리를 넘겨 검색이 실패하던 문제(SQLSTATE 53100): 이 문장에서만 병렬을 끈다.
    sent = []

    def sql(statement):
        sent.append(statement)
        return 'BEGIN\nSET\n[{"run_id": "r", "payload": {"entry_id": "a"}, "distance": 0.1, "entry_key": "a"}, \n {"run_id": "r2", "payload": {"entry_id": "b [x]"}, "distance": 0.2, "entry_key": "b"}]\nCOMMIT'

    monkeypatch.setattr(dense, "sql", sql)
    rows = dense.search_vector([1, 0, 0], config(), ["run-1"], limit=5)
    assert [r["run_id"] for r in rows] == ["r", "r2"] and rows[1]["payload"]["entry_id"] == "b [x]"
    assert sent[0].startswith("BEGIN;\nSET LOCAL max_parallel_workers_per_gather=2;\n") and sent[0].rstrip().endswith("COMMIT;")
    monkeypatch.setattr(dense, "sql", lambda statement: "[]")
    assert dense.search_vectors([[1, 0, 0], [0, 1, 0]], config(), ["run-1"]) == [[], []]
    # 작업자 수는 환경변수로 정한다. 잘못된 값은 기본값, 범위를 벗어난 값은 0~8로 맞춘다.
    for value, expected in (("0", 0), ("4", 4), ("99", 8), ("-1", 0), ("많이", 2)):
        monkeypatch.setenv("NH_VECTOR_SEARCH_WORKERS", value)
        assert dense.parallel_workers() == expected
    monkeypatch.delenv("NH_VECTOR_SEARCH_WORKERS")
    # 공유 메모리 부족(53100)으로 실패하면 병렬 없이 한 번 다시 실행한다. 다른 오류는 그대로 올린다.
    tried = []

    def short_of_memory(statement):
        tried.append(statement.splitlines()[1])
        if "=0;" not in statement.splitlines()[1]:
            raise dense.LocalPostgresError("53100")
        return "[]"

    monkeypatch.setattr(dense, "sql", short_of_memory)
    assert dense.search_vector([1, 0, 0], config(), ["run-1"]) == []
    assert tried == ["SET LOCAL max_parallel_workers_per_gather=2;", "SET LOCAL max_parallel_workers_per_gather=0;"]

    def broken(statement):
        raise dense.LocalPostgresError("42703")

    monkeypatch.setattr(dense, "sql", broken)
    with pytest.raises(dense.LocalPostgresError):
        dense.search_vector([1, 0, 0], config(), ["run-1"])
