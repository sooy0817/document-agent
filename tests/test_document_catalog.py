"""문서 목록표와 코퍼스 검색 범위: 문서 저장본을 열지 않고 목록·범위를 구하고, 실행 ID를 나열하지 않는다. DB는 모의다."""
import pytest

from doc_understanding.rag import catalog
from doc_understanding.rag.retrieval import dense, repository, scope
from doc_understanding.rag.retrieval.scope import CorpusScope, described, run_list


def assembled():
    from tests.test_document_rag import assembled as build
    return build()


@pytest.fixture
def ready(monkeypatch):
    monkeypatch.setattr(catalog, "ensure", lambda: None)


def test_index_statements_write_the_run_summary_and_reselect_the_current_run():
    doc = assembled()
    statements = catalog.index_statements("run-1", doc, 7)
    row = next(s for s in statements if s.startswith("INSERT INTO rag.document_catalog"))
    assert "'run-1'" in row and f",{len(doc.document_ir.pages)},7,{len(doc.table_structure)}," in row
    assert "ON CONFLICT (run_id) DO UPDATE" in row
    # 같은 문서의 동시 색인이 둘 다 현재 실행이 되지 않게, 줄을 쓰기 전에 문서별 잠금을 잡는다.
    lock = next(i for i, s in enumerate(statements) if "pg_advisory_xact_lock" in s)
    assert doc.document_id in statements[lock] and lock < statements.index(row)
    refresh = statements[-1]
    # 그 문서의 실행만 다시 정한다. 실패·실행 중인 실행은 현재 실행이 되지 않는다.
    assert refresh.startswith("UPDATE rag.document_catalog") and doc.document_id in refresh
    assert "NOT IN ('FAILED','RUNNING')" in refresh and "s.created_at DESC, s.run_id DESC" in refresh


def test_index_writes_the_catalog_in_the_same_transaction(monkeypatch):
    from doc_understanding.rag.indexing import writer
    doc, scripts = assembled(), []
    monkeypatch.setattr(writer, "query", lambda statement: [])
    monkeypatch.setattr(writer, "sql", scripts.append)
    writer.create_index("run-1", doc, "0" * 64)
    script = scripts[0]
    assert script.startswith("BEGIN;") and script.endswith("COMMIT;")
    assert script.index("INSERT INTO rag.search_entry") < script.index("INSERT INTO rag.document_catalog")


def test_listing_and_backfill_read_document_payloads_only_once_per_process(monkeypatch):
    scripts, queries = [], []
    monkeypatch.setattr(catalog, "sql", scripts.append)
    monkeypatch.setattr(catalog, "query", lambda statement: queries.append(statement) or [])
    monkeypatch.setattr(catalog, "_ready", __import__("threading").Event())
    catalog.current_runs()
    catalog.current_runs()
    # 채우기(문서 저장본을 읽는 유일한 문장)는 한 번만, 목록 조회는 저장본(staging.artifact)을 읽지 않는다.
    assert len(scripts) == 1 and "staging.artifact" in scripts[0] and "NOT EXISTS" in scripts[0]
    assert len(queries) == 2 and all("staging.artifact" not in q and "payload" not in q for q in queries)


def test_corpus_scope_is_a_catalog_condition_not_a_list_of_run_ids(monkeypatch, ready):
    monkeypatch.setattr(scope, "query", lambda statement: [{"n": 3}])
    corpus = CorpusScope("document-rag/x")
    inner = run_list(corpus)
    assert inner.startswith("SELECT c.run_id FROM rag.document_catalog") and "c.is_current" in inner
    assert "'document-rag/x'" in inner and run_list(["a", "b'c"]) == "'a','b''c'"
    assert len(corpus) == 3 and bool(corpus)
    assert described(corpus) == dict(scope="corpus", index_version="document-rag/x", documents=3)
    assert described(["a"]) == ["a"]
    # 검색 문장: 키워드·벡터·커버리지 모두 같은 하위 질의로 범위를 거른다.
    assert dense.scope_runs(run_ids=corpus) is corpus
    assert inner in repository._restriction(None, corpus)
    statements = []
    monkeypatch.setattr(dense, "query", lambda statement: statements.append(statement) or [dict(total=0, embedded=0)])
    monkeypatch.setattr(dense, "serial_query", lambda statement: statements.append(statement) or [])
    from doc_understanding.rag.embedding.models import EmbeddingConfig
    config = EmbeddingConfig(model="m", dimensions=2, input_usd_per_million=0.0, max_cost_usd=0.1, max_calls=1)
    dense.coverage(corpus, config)
    dense.search_vector([0.1, 0.2], config, corpus, 3)
    assert len(statements) == 2 and all(f"e.run_id IN ({inner})" in s for s in statements)


def test_empty_corpus_scope_is_falsy(monkeypatch, ready):
    monkeypatch.setattr(scope, "query", lambda statement: [{"n": 0}])
    assert not CorpusScope("v") and dense.coverage(CorpusScope("v"), None) == dict(total=0, embedded=0)


def test_find_pages_by_name_and_bounds_the_page_size(monkeypatch, ready):
    calls = []

    def fake(statement, params):
        calls.append((statement, params))
        return [{"n": 250}] if statement.startswith("SELECT count(*)") else []

    monkeypatch.setattr(catalog, "query_params", fake)
    page = catalog.find("v", "  농협 ", limit=5000, offset=-3)
    assert page["total"] == 250 and page["limit"] == 100 and page["offset"] == 0
    assert calls[0][1] == ["v", "농협"] and "LIMIT 100 OFFSET 0" in calls[1][0]
    assert "s.index_version=%s" in calls[0][0]
    catalog.find("v", None, stale=True)
    assert "s.index_version<>%s" in calls[2][0] and calls[2][1] == ["v"]
