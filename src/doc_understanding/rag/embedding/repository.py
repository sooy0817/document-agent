# 역할: 임베딩을 검색 항목·입력 해시와 연결하고 배치별 원자적 저장·재실행을 지원한다.
import json
from pathlib import Path

from doc_understanding.rag.embedding.models import input_hash, validate_vector
from doc_understanding.rag.indexing.writer import INDEX_VERSION
from doc_understanding.storage.staging.local import js, literal, query, sql, sql_params


def migrate():
    from doc_understanding.rag.repository import migrate as migrate_rag

    migrate_rag()
    if not query("SELECT name FROM pg_available_extensions WHERE name='vector'"):
        raise ValueError("PGVECTOR_EXTENSION_NOT_AVAILABLE")
    sql(
        "BEGIN;\n"
        + Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        + "\nCOMMIT;"
    )


def load_entries(run_id):
    snapshots = query(
        f"SELECT s.index_version,r.status FROM rag.document_snapshot s "
        f"JOIN staging.processing_run r ON r.run_id=s.run_id WHERE s.run_id={literal(run_id)}"
    )
    if (
        len(snapshots) != 1
        or snapshots[0]["index_version"] != INDEX_VERSION
        or snapshots[0]["status"] in ("FAILED", "RUNNING")
    ):
        raise ValueError("READY_CURRENT_INDEX_REQUIRED")
    return query(
        f"SELECT entry_id,search_text AS text FROM rag.search_entry "
        f"WHERE run_id={literal(run_id)} ORDER BY entry_id"
    )


def cached_hashes(run_id, config):
    return {
        r["entry_id"]: r["input_sha256"]
        for r in query(
            f"SELECT v.entry_id,v.input_sha256 FROM rag.entry_embedding v "
            f"JOIN rag.search_entry e USING(run_id,entry_id) "
            f"WHERE v.run_id={literal(run_id)} AND v.profile_id={literal(config.profile_id)} "
            f"AND v.profile={js(config.profile)} AND v.dimensions={config.dimensions} "
            f"AND v.index_version={literal(INDEX_VERSION)} AND v.input_text=e.search_text"
        )
    }


def copy_identical(run_id, config):
    """다른 실행에 같은 프로필·같은 입력 텍스트의 벡터가 있으면 이 실행 항목으로 복사한다(API 호출 없음).

    입력 텍스트 원문이 정확히 같아야 하며 현재 인덱스 버전의 완료 실행에만 쓴다. 복사한 수를 반환한다.
    값은 파라미터로 바인딩한다(커넥션 풀이면 서버 바인딩, docker 전송이면 literal 렌더링).
    """
    output = sql_params("""INSERT INTO rag.entry_embedding(run_id,entry_id,profile_id,profile,dimensions,
        input_sha256,input_text,index_version,embedding)
      SELECT DISTINCT ON (e.entry_id) e.run_id,e.entry_id,v.profile_id,v.profile,v.dimensions,
        v.input_sha256,e.search_text,s.index_version,v.embedding
      FROM rag.search_entry e JOIN rag.document_snapshot s ON s.run_id=e.run_id
      JOIN staging.processing_run r ON r.run_id=e.run_id
      JOIN rag.entry_embedding v ON v.profile_id=%s AND v.profile=%s::jsonb
        AND v.dimensions=%s AND v.input_text=e.search_text AND v.run_id<>e.run_id
      WHERE e.run_id=%s AND s.index_version=%s AND r.status NOT IN ('FAILED','RUNNING')
        AND NOT EXISTS (SELECT 1 FROM rag.entry_embedding x WHERE x.run_id=e.run_id
                        AND x.entry_id=e.entry_id AND x.profile_id=%s)
      ORDER BY e.entry_id, v.created_at
      ON CONFLICT(run_id,entry_id,profile_id) DO NOTHING RETURNING 1""",
        [config.profile_id, json.dumps(config.profile, ensure_ascii=False), config.dimensions, run_id,
         INDEX_VERSION, config.profile_id])
    # psql은 결과 행 뒤에 'INSERT 0 n' 상태 줄도 출력하므로 RETURNING 값('1') 줄만 센다.
    return sum(1 for line in output.splitlines() if line.strip() == "1")


def vector_literal(vector, dimensions):
    return literal(json.dumps(validate_vector(vector, dimensions), allow_nan=False)) + "::vector"


def save_batch(run_id, config, items):
    if not items:
        return
    if len({item["entry_id"] for item in items}) != len(items):
        raise ValueError("DUPLICATE_EMBEDDING_ENTRY")
    statements = [
        "BEGIN;",
        "CREATE TEMP TABLE embedding_guard (ok boolean NOT NULL CHECK(ok)) ON COMMIT DROP;",
    ]
    for item in items:
        vector = vector_literal(item["vector"], config.dimensions)
        # 모델 응답 중 원문·인덱스가 바뀌면 한 건도 누락 성공으로 처리하지 않고 배치를 롤백한다.
        statements.append(f"""WITH written AS (
          INSERT INTO rag.entry_embedding(run_id,entry_id,profile_id,profile,dimensions,
            input_sha256,input_text,index_version,embedding)
          SELECT e.run_id,e.entry_id,{literal(config.profile_id)},{js(config.profile)},
            {config.dimensions},{literal(input_hash(item["text"]))},e.search_text,s.index_version,{vector}
          FROM rag.search_entry e JOIN rag.document_snapshot s ON s.run_id=e.run_id
          JOIN staging.processing_run r ON r.run_id=e.run_id
          WHERE e.run_id={literal(run_id)} AND e.entry_id={literal(item["entry_id"])}
            AND e.search_text={literal(item["text"])} AND s.index_version={literal(INDEX_VERSION)}
            AND r.status NOT IN ('FAILED','RUNNING')
          ON CONFLICT(run_id,entry_id,profile_id) DO UPDATE SET
            profile=excluded.profile,dimensions=excluded.dimensions,input_sha256=excluded.input_sha256,
            input_text=excluded.input_text,index_version=excluded.index_version,
            embedding=excluded.embedding,created_at=now() RETURNING 1
        ) INSERT INTO embedding_guard SELECT count(*)=1 FROM written;""")
    sql("\n".join(statements + ["COMMIT;"]))
