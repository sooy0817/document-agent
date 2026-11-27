"""마이그레이션 경로 검사와 명시적으로 켠 로컬 pgvector 통합 검사."""

import os
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest


def test_migration_chain_loads_current_schema_locations(monkeypatch):
    statements = []
    bind = SimpleNamespace(exec_driver_sql=statements.append)
    monkeypatch.setitem(
        sys.modules, "alembic", SimpleNamespace(op=SimpleNamespace(get_bind=lambda: bind))
    )
    root = Path(__file__).parents[1]
    versions = root / "migrations" / "versions"
    previous = None
    for name in ("023_local_staging.py", "027_document_rag.py", "028_rag_embeddings.py"):
        module = runpy.run_path(str(versions / name))
        assert module["down_revision"] == previous
        module["upgrade"]()
        previous = module["revision"]
    assert len(statements) == 3
    assert "CREATE TABLE IF NOT EXISTS rag.entry_embedding" in statements[-1]
    assert "ON DELETE CASCADE" in statements[-1]
    assert "vector_dims(embedding)=dimensions" in statements[-1]
    assert "vector_norm(embedding)>0" in statements[-1]


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("NH_TEST_LOCAL_POSTGRES") != "1",
    reason="opt-in local PostgreSQL with pgvector; embeddings always mocked",
)
def test_actual_sql_literal_preserves_mixed_newlines():
    from doc_understanding.storage.staging.local import literal, query

    raw = '첫 줄\n둘째 줄\r\n셋째 줄\r끝'
    rows = query(f"SELECT encode(convert_to({literal(raw)},'UTF8'),'hex') AS raw_hex")
    assert rows == [{'raw_hex': raw.encode('utf-8').hex()}]


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("NH_TEST_LOCAL_POSTGRES") != "1",
    reason="opt-in local PostgreSQL with pgvector; embeddings always mocked",
)
def test_actual_vector_storage_rollback_resume_and_search():
    from doc_understanding.rag.embedding import repository as vectors
    from doc_understanding.rag.embedding.client import EmbeddingClient
    from doc_understanding.rag.embedding.indexer import embed_run
    from doc_understanding.rag import repository
    from doc_understanding.rag.retrieval.dense import coverage, search_vector
    from doc_understanding.storage.staging.local import literal, query, sql
    from tests.test_document_rag import ROOT, GANGDONG
    from tests.test_rag_embeddings import config, response

    # 기본 모델 프로필과 분리한 테스트 전용 벡터만 생성하고 마지막에 제거한다.
    vectors.migrate()
    stored = repository.store(
        GANGDONG / "staging-manifest.json", ROOT / "data/document-rag/gangdong/document-v2.json"
    )
    rid = stored["run_id"]
    cfg = config(profile_version="test-" + uuid4().hex)
    entries = vectors.load_entries(rid)
    try:
        with pytest.raises(RuntimeError, match="LOCAL_POSTGRES_STATEMENT_FAILED") as rejected:
            vectors.save_batch(
                rid,
                cfg,
                [
                    dict(entries[0], vector=[1, 0, 0]),
                    dict(entry_id="nonexistent", text="unavailable", vector=[1, 0, 0]),
                ],
            )
        # 다른 SQL 오류를 의도한 누락 방지·롤백 검사의 성공으로 오인하지 않는다.
        assert rejected.value.sqlstate == '23514'
        assert vectors.cached_hashes(rid, cfg) == {}
        client = EmbeddingClient(cfg, allow_external=True, transport=response)
        result = embed_run(rid, client)
        assert result["written"] > 0
        repeated = embed_run(rid, EmbeddingClient(cfg))
        assert repeated["written"] == 0 and repeated["reused"] == result["written"]
        assert coverage([rid], cfg)["embedded"] == result["written"]
        hits = search_vector([1, 0, 0], cfg, [rid])
        assert hits and all(h["run_id"] == rid for h in hits)
        count = query(
            f"SELECT count(*) n FROM rag.entry_embedding WHERE profile_id={literal(cfg.profile_id)}"
        )[0]["n"]
        assert count == result["written"]
    finally:
        sql(
            f"DELETE FROM rag.entry_embedding WHERE run_id={literal(rid)} AND profile_id={literal(cfg.profile_id)};"
        )


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("NH_TEST_LOCAL_POSTGRES") != "1",
    reason="opt-in local PostgreSQL with pgvector; embeddings always mocked",
)
@pytest.mark.parametrize("transport", ["pool", "docker"])
def test_actual_identical_text_is_copied_across_runs_without_api_calls(tmp_path, monkeypatch, transport):
    monkeypatch.setenv("NH_DB_TRANSPORT", transport)
    from doc_understanding.domain.pipeline_contracts import CanonicalDocument
    from doc_understanding.rag import repository
    from doc_understanding.rag.embedding import repository as vectors
    from doc_understanding.rag.embedding.client import EmbeddingClient
    from doc_understanding.rag.embedding.indexer import embed_run
    from doc_understanding.storage.staging.local import literal, sql
    from tests.test_document_rag import ROOT, GANGDONG
    from tests.test_rag_embeddings import config, response

    vectors.migrate()
    source = ROOT / "data/document-rag/gangdong/document-v2.json"
    first = repository.store(GANGDONG / "staging-manifest.json", source)["run_id"]
    # 검색 항목은 같고 문서 바이트만 다른 두 번째 실행(원본 URI 표기만 다름)을 만든다.
    doc = CanonicalDocument.model_validate_json(source.read_bytes())
    variant = tmp_path / "document-variant.json"
    variant.write_text(doc.model_copy(update={"source_uri": "copy-test-" + uuid4().hex}).model_dump_json(),
                       encoding="utf-8")
    second = repository.store(GANGDONG / "staging-manifest.json", variant)["run_id"]
    assert second != first
    cfg = config(profile_version="copy-test-" + uuid4().hex)
    try:
        written = embed_run(first, EmbeddingClient(cfg, allow_external=True, transport=response))["written"]
        copied = embed_run(second, EmbeddingClient(cfg))  # allow_external=False: API 호출 시 실패한다.
        assert copied["copied_from_other_runs"] == written and copied["written"] == 0
        assert copied["coverage_complete"] and copied["calls"] == []
    finally:
        sql(f"DELETE FROM rag.entry_embedding WHERE profile_id={literal(cfg.profile_id)};")
