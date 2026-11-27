"""검색어 일괄 검색: 결과는 하나씩 검색한 것과 같고 임베딩·SQL 호출 수만 줄어든다."""
import os

import pytest

from doc_understanding.rag.retrieval import dense, runtime
from doc_understanding.rag.retrieval import repository as lexical_repo
from doc_understanding.rag.retrieval.runtime import Retriever


class FakeClient:
    def __init__(self):
        from doc_understanding.rag.embedding.models import EmbeddingConfig
        self.config = EmbeddingConfig(dimensions=3, input_usd_per_million=0.0 + 1, max_cost_usd=1)
        self.audit, self.calls = [], []
        self.reserved_cost_usd = 0

    def embed(self, texts):
        self.calls.append(list(texts))
        return [[1.0, float(len(t)), 0.0] for t in texts]


def hit(entry, score):
    return dict(run_id="r", payload={"entry_id": entry}, score=score)


@pytest.fixture
def fake_backend(monkeypatch):
    lexical = {"a": [hit("x", 2), hit("y", 1)], "b": [hit("y", 3)]}
    vectors = {1.0: [hit("y", 0.9), hit("z", 0.5)], 2.0: [hit("x", 0.8)]}
    sql_calls = []
    monkeypatch.setattr(dense, "scope_runs", lambda run_id, run_ids=None: ["r"])
    monkeypatch.setattr(dense, "coverage", lambda scope, cfg: dict(total=3, embedded=3))
    monkeypatch.setattr(runtime, "lexical_search", lambda q, limit, run_ids: lexical.get(q, []))
    monkeypatch.setattr(dense, "search_vector", lambda v, cfg, scope, n: vectors.get(v[1], []))

    def search_vectors(vs, cfg, scope, n):
        sql_calls.append("vectors")
        return [vectors.get(v[1], []) for v in vs]

    def search_many(qs, limit, run_ids):
        sql_calls.append("lexical")
        return [lexical.get(q, []) for q in qs]

    monkeypatch.setattr(dense, "search_vectors", search_vectors)
    monkeypatch.setattr(lexical_repo, "search_many", search_many)
    return sql_calls


@pytest.mark.parametrize("mode", ["hybrid", "dense"])
def test_search_many_equals_one_by_one_with_single_embedding_call(fake_backend, mode):
    one_client, many_client = FakeClient(), FakeClient()
    one = [Retriever(mode, one_client)(q, None, 5, run_ids=["r"]) for q in ("a", "b")]
    many = Retriever(mode, many_client).search_many(["a", "b"], None, 5, run_ids=["r"])
    assert many == one
    assert len(one_client.calls) == 2 and many_client.calls == [["a", "b"]]
    assert fake_backend.count("vectors") == 1


def test_search_many_reports_lexical_only_when_no_vectors(monkeypatch, fake_backend):
    monkeypatch.setattr(dense, "coverage", lambda scope, cfg: dict(total=3, embedded=0))
    client = FakeClient()
    out = Retriever("hybrid", client).search_many(["a", "b"], None, 5, run_ids=["r"])
    assert [h["retrieval"]["method"] for h in out[0]] == ["lexical_only_no_embeddings"] * 2 and client.calls == []


def test_lexical_score_recognizes_syllable_spaced_labels_once():
    from doc_understanding.rag.retrieval.lexical import lexical_score

    query = "평균처리시간"
    plain = lexical_score(query, query)
    assert plain > 0
    assert lexical_score("평 균 처 리 시 간", query) == plain
    assert lexical_score("평균처리시간 (평 균 처 리 시 간)", query) == plain
    assert lexical_score("평 균\n처 리 시 간", query) < plain
    assert lexical_score("1 2 3", "123") == 0


def test_lexical_batch_sql_keeps_query_order_and_skips_empty_queries(monkeypatch):
    seen = []
    rows = [dict(query_no=1, run_id="r", payload={"entry_id": "b1"}, score=1.0),
            dict(query_no=0, run_id="r", payload={"entry_id": "a1"}, score=2.0)]
    monkeypatch.setattr(lexical_repo, "query", lambda statement: seen.append(statement) or rows)
    out = lexical_repo.search_many(["만기 이자율", "!!!", "이자율"], 5, run_ids=["r"])
    assert len(seen) == 1 and seen[0].count("UNION ALL") == 1  # 검색어가 없는 두 번째 질의는 빠진다
    assert out[0][0]["payload"]["entry_id"] == "a1" and out[1][0]["payload"]["entry_id"] == "b1" and out[2] == []
    assert "query_no" not in out[0][0]


def test_scoped_retriever_exposes_batch_search(monkeypatch):
    from doc_understanding.services import web_rag
    calls = []
    monkeypatch.setattr(web_rag.repository, "search_many", lambda qs, limit, run_ids: calls.append(run_ids) or [[]] * len(qs))
    search = web_rag.scoped(None, ["r1", "r2"])
    assert search.many(["a", "b"], 4) == [[], []] and calls == [["r1", "r2"]]


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("NH_TEST_LOCAL_POSTGRES") != "1", reason="opt-in local PostgreSQL")
def test_actual_lexical_batch_equals_single_searches():
    from doc_understanding.rag.retrieval.repository import search, search_many
    questions = ["이자율", "만기 2개월 이자율", "중도해지 이율"]
    assert search_many(questions, 10) == [search(q, None, 10) for q in questions]
