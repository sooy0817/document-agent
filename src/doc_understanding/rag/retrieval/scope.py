# 역할: 검색 범위. 실행 ID 목록, 또는 "코퍼스 전체"(문서 목록표 조건으로 거르며 실행 ID를 나열하지 않는다).
from doc_understanding.storage.staging.local import literal, query


class CorpusScope:
    """현재 색인 버전의 검색 대상 문서 전체.

    문서가 늘어도 질문마다 실행 ID 수천 개를 주고받거나 SQL에 나열하지 않도록, 범위를 목록표 하위 질의로 표현한다.
    결과는 같은 실행들을 ID로 나열했을 때와 같다.
    """

    def __init__(self, index_version):
        self.index_version = index_version
        self._count = None

    def sql(self):
        from doc_understanding.rag import catalog
        catalog.ensure()
        return catalog.scope_sql(self.index_version)

    def __len__(self):
        if self._count is None:
            self._count = query(f"SELECT count(*) n FROM ({self.sql()}) scope")[0]["n"]
        return self._count

    def __bool__(self):
        return len(self) > 0


def run_list(run_ids):
    """`run_id IN (...)` 안에 넣을 내용: ID 나열 또는 코퍼스 범위 하위 질의."""
    if isinstance(run_ids, CorpusScope):
        return run_ids.sql()
    return ",".join(literal(r) for r in run_ids)


def described(scope):
    """감사 기록용 범위 표현. 코퍼스 전체는 실행 ID 대신 조건과 문서 수를 남긴다."""
    if isinstance(scope, CorpusScope):
        return dict(scope="corpus", index_version=scope.index_version, documents=len(scope))
    return scope
