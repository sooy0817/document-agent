# 역할: 검색 대상 실행을 고정하고 같은 임베딩 프로필의 코사인 유사도로 후보를 찾는다.
from doc_understanding.rag.embedding.repository import vector_literal
from doc_understanding.rag.retrieval.scope import CorpusScope, run_list
import json
import os

from doc_understanding.storage.staging.local import LocalPostgresError, js, literal, query, sql


def scope_runs(run_id=None, *, run_ids=None):
    if isinstance(run_ids, CorpusScope):
        return run_ids  # 실행 ID를 나열하지 않는다. 범위는 SQL 안에서 목록표 조건으로 거른다.
    if run_ids is not None and not run_ids:
        return []
    restriction = (
        "AND s.run_id IN (" + ",".join(literal(r) for r in run_ids) + ")"
        if run_ids is not None
        else f"AND s.run_id={literal(run_id)}"
        if run_id
        else ""
    )
    distinct = "" if restriction else "DISTINCT ON (s.document_id) "
    rows = query(
        f"SELECT {distinct}s.run_id FROM rag.document_snapshot s "
        f"JOIN staging.processing_run r ON r.run_id=s.run_id "
        f"WHERE r.status NOT IN ('FAILED','RUNNING') {restriction} "
        "ORDER BY s.document_id,s.created_at DESC,s.run_id DESC"
    )
    return [row["run_id"] for row in rows]


def _join(config):
    # 오래된 입력이나 다른 모델·차원·재색인 버전의 벡터를 섞지 않는다.
    return (
        f"v.run_id=e.run_id AND v.entry_id=e.entry_id "
        f"AND v.profile_id={literal(config.profile_id)} AND v.profile={js(config.profile)} "
        f"AND v.dimensions={config.dimensions} AND v.index_version=s.index_version "
        "AND v.input_text=e.search_text"
    )


def coverage(run_ids, config):
    if not run_ids:
        return dict(total=0, embedded=0)
    restriction = run_list(run_ids)
    return query(
        f"SELECT count(*) total,count(v.entry_id) embedded FROM rag.search_entry e "
        f"JOIN rag.document_snapshot s ON s.run_id=e.run_id "
        f"JOIN staging.processing_run r ON r.run_id=e.run_id "
        f"LEFT JOIN rag.entry_embedding v ON {_join(config)} "
        f"WHERE e.run_id IN ({restriction}) AND r.status NOT IN ('FAILED','RUNNING')"
    )[0]


def vector_sql(vector, config, run_ids, limit):
    """코사인 정확 검색 SELECT 문장 하나. 정렬용 distance 열을 함께 낸다."""
    encoded = vector_literal(vector, config.dimensions)
    restriction = run_list(run_ids)
    # 우선 정확 검색을 기준선으로 둔다. ANN 인덱스 도입은 검색량·회수율 측정 후 별도로 한다.
    return (
        f"SELECT e.run_id,e.payload,1-(v.embedding <=> {encoded}) AS score,"
        f"(v.embedding <=> {encoded}) AS distance,e.entry_id AS entry_key "
        f"FROM rag.search_entry e JOIN rag.document_snapshot s ON s.run_id=e.run_id "
        f"JOIN staging.processing_run r ON r.run_id=e.run_id "
        f"JOIN rag.entry_embedding v ON {_join(config)} "
        f"WHERE e.run_id IN ({restriction}) AND r.status NOT IN ('FAILED','RUNNING') "
        f"ORDER BY v.embedding <=> {encoded},e.run_id,e.entry_id "
        f"LIMIT {max(1, min(int(limit), 30))}"
    )


DEFAULT_WORKERS = 2  # 문서 112개·검색 항목 3.4만 개에서 4.6초 → 1.1초(2026-11-13 측정). 4로 늘려도 더 빨라지지 않았다.


def parallel_workers():
    """벡터 검색 문장에 허용할 병렬 작업자 수(환경변수 NH_VECTOR_SEARCH_WORKERS, 기본 2, 0 = 병렬 없음).

    작업자마다 컨테이너 공유 메모리를 쓴다. 기본 64MB에서는 검색 항목 3만 개에서 문장이 실패했다(SQLSTATE 53100).
    그래서 compose.yaml이 shm_size를 1GB로 둔다. 잘못된 값은 기본값으로 본다."""
    try:
        return max(0, min(int(os.environ.get("NH_VECTOR_SEARCH_WORKERS", DEFAULT_WORKERS)), 8))
    except ValueError:
        return DEFAULT_WORKERS


def serial_query(statement, workers=None):
    """벡터 검색 문장을 정해진 병렬 작업자 수로 실행한다. 공유 메모리가 모자라 실패하면 병렬 없이 한 번 다시 실행한다
    (shm_size를 늘리기 전에 만든 컨테이너에서도 검색이 멈추지 않게 한다).

    정확 검색 결과는 병렬 여부와 무관하다. SET LOCAL이라 이 트랜잭션에만 적용되고 연결에 남지 않는다.
    """
    workers = parallel_workers() if workers is None else workers
    try:
        text = sql(f"BEGIN;\nSET LOCAL max_parallel_workers_per_gather={workers};\n"
                   f"SELECT coalesce(json_agg(q),'[]'::json)::text FROM ({statement}) q;\nCOMMIT;")
    except LocalPostgresError as error:
        if workers and getattr(error, "sqlstate", None) == "53100":
            return serial_query(statement, workers=0)
        raise
    # 전송 방식에 따라 BEGIN/SET 같은 명령 표시가 섞일 수 있어 JSON 배열 부분만 읽는다.
    return json.loads(text[text.index("["):text.rindex("]") + 1])


def _public(rows):
    return [{k: v for k, v in r.items() if k not in ("distance", "entry_key", "query_no")} for r in rows]


def search_vector(vector, config, run_ids, limit=8):
    if not run_ids:
        return []
    return _public(serial_query(vector_sql(vector, config, run_ids, limit)))


def search_vectors(vectors, config, run_ids, limit=8):
    """여러 질문 벡터의 검색을 SQL 한 번으로 실행한다. 결과는 벡터별 search_vector()와 같다."""
    out = [[] for _ in vectors]
    if not run_ids or not vectors:
        return out
    union = " UNION ALL ".join(f"(SELECT {i} AS query_no, x.* FROM ({vector_sql(v, config, run_ids, limit)}) x)"
                               for i, v in enumerate(vectors))
    for row in serial_query(f"SELECT * FROM ({union}) u ORDER BY query_no, distance, run_id, entry_key"):
        out[row["query_no"]].append(row)
    return [_public(rows) for rows in out]
