# 역할: 선택한 검색 모드와 임베딩 호출을 연결하고 실제 적용 범위·누락·실패를 기록한다.
from doc_understanding.rag.retrieval import dense
from doc_understanding.rag.retrieval.hybrid import fuse
from doc_understanding.rag.retrieval.repository import search as lexical_search
from doc_understanding.rag.retrieval.scope import described


class Retriever:
    def __init__(self, mode, client):
        if mode not in ("dense", "hybrid"):
            raise ValueError("INVALID_VECTOR_RETRIEVAL_MODE")
        self.mode, self.client = mode, client
        self.audit = []

    def __call__(self, question, run_id=None, limit=8, *, run_ids=None):
        record = dict(mode=self.mode, status="started", profile_id=self.client.config.profile_id)
        self.audit.append(record)
        try:
            scope = dense.scope_runs(run_id, run_ids=run_ids)
            record["run_ids"] = described(scope)
            record["coverage"] = dense.coverage(scope, self.client.config)
            count = max(1, min(int(limit), 30))
            candidates = min(count * 3, 30)
            lexical = (
                lexical_search(question, limit=candidates, run_ids=scope)
                if self.mode == "hybrid"
                else []
            )
            if not scope:
                record["status"] = "empty_scope"
                return []
            if not record["coverage"]["embedded"]:
                record["status"] = "no_matching_embeddings"
                if self.mode == "dense":
                    raise ValueError("NO_MATCHING_EMBEDDINGS")
                # 벡터가 없는 경우의 키워드 전용 결과임을 감사 기록과 결과에 명시한다.
                return [
                    dict(h, retrieval={"method": "lexical_only_no_embeddings"})
                    for h in lexical[:count]
                ]
            vector = self.client.embed([question])[0]
            hits = dense.search_vector(vector, self.client.config, scope, candidates)
            record.update(
                status="completed", lexical_candidates=len(lexical), dense_candidates=len(hits)
            )
            if self.mode == "hybrid":
                return fuse(lexical, hits, count)
            return [dict(h, retrieval={"method": "dense"}) for h in hits[:count]]
        except Exception as error:
            record.update(status="failed", error_type=type(error).__name__)
            raise

    def search_many(self, questions, run_id=None, limit=8, *, run_ids=None):
        """여러 검색어를 한꺼번에 검색한다. 임베딩 API 1회(묶음), 범위·커버리지·키워드·벡터 SQL 각 1회.

        검색어별 결과는 __call__을 하나씩 부른 결과와 같다(같은 SQL 조각과 같은 RRF 결합).
        """
        from doc_understanding.rag.retrieval.repository import search_many as lexical_many

        questions = list(questions)
        record = dict(mode=self.mode, status="started", profile_id=self.client.config.profile_id,
                      batched_queries=len(questions))
        self.audit.append(record)
        empty = [[] for _ in questions]
        try:
            scope = dense.scope_runs(run_id, run_ids=run_ids)
            record["run_ids"] = described(scope)
            record["coverage"] = dense.coverage(scope, self.client.config)
            count = max(1, min(int(limit), 30))
            candidates = min(count * 3, 30)
            if not scope:
                record["status"] = "empty_scope"
                return empty
            lexical = lexical_many(questions, candidates, run_ids=scope) if self.mode == "hybrid" else empty
            if not record["coverage"]["embedded"]:
                record["status"] = "no_matching_embeddings"
                if self.mode == "dense":
                    raise ValueError("NO_MATCHING_EMBEDDINGS")
                return [[dict(h, retrieval={"method": "lexical_only_no_embeddings"}) for h in hits[:count]]
                        for hits in lexical]
            size = self.client.config.batch_size
            vectors = [v for i in range(0, len(questions), size) for v in self.client.embed(questions[i:i + size])]
            dense_hits = dense.search_vectors(vectors, self.client.config, scope, candidates)
            record.update(status="completed", lexical_candidates=[len(h) for h in lexical],
                          dense_candidates=[len(h) for h in dense_hits])
            if self.mode == "hybrid":
                return [fuse(lex, den, count) for lex, den in zip(lexical, dense_hits)]
            return [[dict(h, retrieval={"method": "dense"}) for h in hits[:count]] for hits in dense_hits]
        except Exception as error:
            record.update(status="failed", error_type=type(error).__name__)
            raise

    def report(self):
        return dict(
            retrieval=self.audit,
            embedding_calls=self.client.audit,
            embedding_cost_complete=all("estimated_cost_usd" in c for c in self.client.audit),
            estimated_embedding_cost_usd=sum(
                c.get("estimated_cost_usd", 0) for c in self.client.audit
            ),
            reserved_embedding_cost_usd=self.client.reserved_cost_usd,
        )
