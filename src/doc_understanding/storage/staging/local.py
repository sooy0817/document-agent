"""Local PostgreSQL transport: pooled connection when available, Docker psql otherwise.

- pool: psycopg 커넥션 풀(연결 유지). SQL 1회 수 ms. psycopg가 설치되어 있고 로컬 DB에 연결될 때 쓴다.
- docker: 기존 `docker compose exec psql`(SQL은 stdin으로, 셸 문자열 끼워넣기 없음). SQL 1회 약 0.27초.
NH_DB_TRANSPORT=auto(기본)|pool|docker 로 고른다. auto는 풀 연결에 실패하면 docker로 대체한다.
두 방식은 같은 sql()/query() 결과를 돌려주며 오류는 SQLSTATE만 노출한다.
"""
import json
import os
import re
import subprocess
import threading
from pathlib import Path


def literal(value):
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def js(value):
    return literal(json.dumps(value, ensure_ascii=False, allow_nan=False)) + "::jsonb"


class LocalPostgresError(RuntimeError):
    """원문·SQL·자격 증명을 노출하지 않는 로컬 DB 오류 코드다."""

    def __init__(self, sqlstate=None):
        self.sqlstate = sqlstate
        super().__init__('LOCAL_POSTGRES_STATEMENT_FAILED' + (
            f' SQLSTATE={sqlstate}' if sqlstate else ''))


_POOL = None
_POOL_STATE = "unknown"  # unknown | ready | unavailable
_POOL_LOCK = threading.Lock()


def _conninfo():
    """접속 정보: 환경변수 DATABASE_URL 또는 POSTGRES_*, 없으면 프로젝트 .env. localhost는 127.0.0.1로 바꾼다."""
    values = dict(os.environ)
    env_file = Path(__file__).resolve().parents[4] / ".env"
    if "DATABASE_URL" not in values and env_file.is_file():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values.setdefault(key.strip(), value.strip().strip("\"'"))
    url = values.get("DATABASE_URL")
    if not url:
        return None
    # SQLAlchemy 형식(postgresql+psycopg://)을 libpq 형식으로. Windows에서 localhost는 IPv6 해석으로 멈출 수 있다.
    url = re.sub(r"^postgresql\+\w+://", "postgresql://", url)
    return re.sub(r"@localhost(?=[:/])", "@127.0.0.1", url)


def _pool():
    global _POOL, _POOL_STATE
    mode = os.environ.get("NH_DB_TRANSPORT", "auto")
    if mode == "docker":
        return None
    if _POOL_STATE == "ready":
        return _POOL
    if _POOL_STATE == "unavailable" and mode == "auto":
        return None
    with _POOL_LOCK:
        if _POOL_STATE == "ready":
            return _POOL
        try:
            from psycopg_pool import ConnectionPool

            conninfo = _conninfo()
            if not conninfo:
                raise RuntimeError("DATABASE_URL_UNAVAILABLE")
            pool = ConnectionPool(
                conninfo, min_size=1, max_size=4, open=False, timeout=10,
                kwargs=dict(autocommit=True, connect_timeout=3),
                configure=lambda conn: conn.execute("SET standard_conforming_strings=on"))
            pool.open(wait=True, timeout=5)
            with pool.connection() as conn:
                if conn.execute("SELECT current_database()").fetchone()[0] != "nh_document":
                    pool.close()
                    raise RuntimeError("LOCAL_DATABASE_GUARD")
            _POOL, _POOL_STATE = pool, "ready"
            return pool
        except Exception:
            _POOL_STATE = "unavailable"
            if mode == "pool":
                raise LocalPostgresError(None) from None
            return None


def close_pool():
    """서버 종료 시 풀을 닫는다. 다음 호출은 다시 연결을 시도한다."""
    global _POOL, _POOL_STATE
    with _POOL_LOCK:
        if _POOL is not None:
            _POOL.close()
        _POOL, _POOL_STATE = None, "unknown"


def transport():
    return "pool" if _pool() is not None else "docker"


def _pool_sql(pool, statement):
    import psycopg

    with pool.connection() as conn:
        try:
            cur = conn.execute(statement)  # 파라미터가 없으면 여러 문장 스크립트를 그대로 실행한다.
            lines = []
            while True:
                if cur.description:
                    lines += ["|".join("" if v is None else str(v) for v in row) for row in cur.fetchall()]
                if not cur.nextset():
                    break
            return "\n".join(lines)
        except psycopg.Error as error:
            # 스크립트 중간 실패로 남은 트랜잭션을 정리해 연결을 풀에 깨끗하게 돌려준다.
            if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                conn.execute("ROLLBACK")
            raise LocalPostgresError(error.sqlstate) from None


def sql(statement):
    pool = _pool()
    if pool is not None:
        return _pool_sql(pool, statement)
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "psql", "-X",
         "-U", "nh_document", "-d", "nh_document", "-v", "ON_ERROR_STOP=1",
         "-v", "VERBOSITY=sqlstate", "-At"],
        # Windows 텍스트 모드는 SQL 리터럴 안의 LF/CRLF도 변환하므로 바이트로 전송한다.
        input=("SET standard_conforming_strings=on;\n" + statement).encode("utf-8"),
        capture_output=True, check=False,
    )
    if result.returncode:
        # 서버 상세 메시지 대신 고정 길이 SQLSTATE만 노출한다. Docker 오류는 일반 코드로 남긴다.
        match = re.search(r'(?:ERROR|FATAL|PANIC):\s+([0-9A-Z]{5})(?:\s|$)',
                          result.stderr.decode('utf-8', errors='replace'))
        raise LocalPostgresError(match.group(1) if match else None)
    return "\n".join(x for x in result.stdout.decode('utf-8').splitlines() if x != "SET")


PLACEHOLDER = re.compile(r"%%|%s")


def render(statement, params):
    """docker 전송용: %s 자리에 값을 literal()로 넣는다. %%는 %로 바꾼다. 개수가 다르면 거부한다."""
    values = iter(params)

    def value(v):
        if v is None:
            return "NULL"
        if isinstance(v, bool):
            return "TRUE" if v else "FALSE"
        if isinstance(v, (int, float)):
            return repr(v)
        if isinstance(v, (list, tuple)):
            return "ARRAY[" + ",".join(value(x) for x in v) + "]" + ("::text[]" if all(isinstance(x, str) for x in v) else "")
        return literal(v)

    def replace(match):
        if match.group(0) == "%%":
            return "%"
        try:
            return value(next(values))
        except StopIteration:
            raise ValueError("SQL_PARAMETER_COUNT_MISMATCH") from None

    out = PLACEHOLDER.sub(replace, statement)
    if next(values, PLACEHOLDER) is not PLACEHOLDER:
        raise ValueError("SQL_PARAMETER_COUNT_MISMATCH")
    return out


def sql_params(statement, params):
    """문장 하나를 파라미터와 함께 실행한다. 풀이면 서버 바인딩, docker면 literal() 렌더링."""
    pool = _pool()
    if pool is None:
        return sql(render(statement, params))
    import psycopg

    with pool.connection() as conn:
        try:
            cur = conn.execute(statement, list(params))
            return "\n".join("|".join("" if v is None else str(v) for v in row)
                             for row in (cur.fetchall() if cur.description else []))
        except psycopg.Error as error:
            if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                conn.execute("ROLLBACK")
            raise LocalPostgresError(error.sqlstate) from None


def query_params(statement, params):
    return json.loads(sql_params(f"SELECT coalesce(json_agg(q),'[]'::json)::text FROM ({statement}) q", params))


def migrate():
    if sql("SELECT current_database();").strip() != "nh_document":
        raise RuntimeError("LOCAL_DATABASE_GUARD")
    return sql("BEGIN;\n" + Path(__file__).with_name("schema.sql").read_text() + "\nCOMMIT;")


def query(statement):
    # ::text로 받아 두 전송 방식이 같은 JSON 문자열을 돌려주게 한다(psycopg는 json을 객체로 바꾼다).
    return json.loads(sql(f"SELECT coalesce(json_agg(q),'[]'::json)::text FROM ({statement}) q;"))


def insert(table, values):
    return f"INSERT INTO staging.{table} ({','.join(values)}) VALUES ({','.join(values.values())});"


def ingest(ir, layout, run_dir, original, key, run_id, artifacts, fail=False, detail=True, payload_ids=None):
    """Atomic records; request key is explicit and successful replays are no-ops.

    detail=False는 staging.evidence·content_block·relation_candidate에 쓰지 않는다(ADR-080 1단계).
    이 세 표는 검증 스크립트 말고는 읽는 곳이 없고, 같은 내용이 artifact의 문서 기록에 들어 있다.
    참조 검사(DANGLING_LAYOUT_REFERENCE)와 page·artifact 기록은 그대로다.

    payload_ids를 주면 그 artifact의 내용만 DB에 넣는다(ADR-080 2단계). 나머지 artifact는 줄(종류·해시·파일 경로)만
    남기고 payload는 빈 값이다. 원본 분석 기록·물리 표·구조 표는 문서 기록 안에 이미 들어 있어 따로 넣으면 두 벌이 된다.
    파일 내용과 해시 검사, 재실행 때의 해시 대조는 모든 artifact에 그대로 적용된다.
    """
    from hashlib import sha256
    from time import perf_counter
    started = perf_counter()
    source_id = sha256(str(original.resolve()).encode()).hexdigest()
    version_id = source_id + ":" + ir.source_sha256
    if sha256(original.read_bytes()).hexdigest() != ir.source_sha256:
        raise ValueError("SOURCE_CHANGED")
    if layout.document_id != ir.document_id or len(layout.pages) != len(ir.pages):
        raise ValueError("LAYOUT_SOURCE_MISMATCH")
    valid_refs = {p.page_id for p in ir.pages}
    valid_refs.update(b.block_id for p in ir.pages for b in p.blocks)
    valid_refs.update(t.table_candidate_id for p in ir.pages for t in p.tables)
    valid_refs.update(aid for aid, _, _, _, pid in artifacts if pid)
    if any(r.source not in valid_refs or r.target not in valid_refs for r in layout.relations):
        raise ValueError("DANGLING_LAYOUT_REFERENCE")
    for _, _, path, payload, _ in artifacts:
        if json.loads(path.read_text(encoding="utf-8")) != payload:
            raise ValueError("ARTIFACT_CONTENT_MISMATCH")
    existing = query(f"SELECT run_id,status,version_id FROM staging.processing_run WHERE idempotency_key={literal(key)}")
    if existing:
        if existing[0]["version_id"] != version_id:
            raise ValueError("IDEMPOTENCY_SOURCE_CONFLICT")
        if existing[0]["status"] in ("FAILED", "RUNNING"):
            raise ValueError("FAILED_OR_INTERRUPTED_ATTEMPT_REQUIRES_NEW_KEY")
        saved = query(f"SELECT artifact_id,sha256 FROM staging.artifact WHERE run_id={literal(existing[0]['run_id'])}")
        if {r["artifact_id"]: r["sha256"] for r in saved} != {
            aid: sha256(p.read_bytes()).hexdigest() for aid, _, p, _, _ in artifacts
        }:
            raise ValueError("IDEMPOTENCY_ARTIFACT_CONFLICT")
        return {"duplicate": True, "run_id": existing[0]["run_id"]}
    sql("BEGIN;\n" + insert("source_document", {
        "source_id": literal(source_id), "original_path": literal(original.resolve())
    }).replace(");", ") ON CONFLICT DO NOTHING;") + insert("document_version", {
        "version_id": literal(version_id), "source_id": literal(source_id),
        "sha256": literal(ir.source_sha256), "bytes": str(original.stat().st_size),
    }).replace(");", ") ON CONFLICT DO NOTHING;") + insert("processing_run", {
        "run_id": literal(run_id), "version_id": literal(version_id),
        "idempotency_key": literal(key), "status": literal("RUNNING"),
    }) + "\nCOMMIT;")
    statements = ["BEGIN;"]
    def add(table, **values):
        statements.append(insert(table, values))
    evidence_ids = set()
    def evidence(eid, pid, kind, payload):
        if eid in evidence_ids:
            return
        evidence_ids.add(eid)
        if detail:
            add("evidence", run_id=literal(run_id), evidence_id=literal(eid),
                page_id=literal(pid), kind=literal(kind), payload=js(payload()))
    for page, diagnosis in zip(ir.pages, layout.pages, strict=True):
        add("page", run_id=literal(run_id), page_id=literal(page.page_id),
            page_number=str(page.page_number), status=literal(diagnosis.status),
            diagnosis=js(diagnosis.model_dump(mode="json")))
        # payload는 쓸 때만 만든다(도형이 많은 문서에서는 이 직렬화가 가장 크다).
        evidence(page.page_id, page.page_id, "page",
                 lambda page=page: {"page_number": page.page_number, "image": page.image.model_dump(mode="json")})
        for block in page.blocks:
            evidence(block.block_id, page.page_id, block.kind, lambda item=block: item.model_dump(mode="json"))
            for span in block.spans:
                evidence(span.span_id, page.page_id, "span", lambda item=span: item.model_dump(mode="json"))
        for table in page.tables:
            evidence(table.table_candidate_id, page.page_id, "table", lambda item=table: item.model_dump(mode="json"))
            for cell in table.cells:
                evidence(cell.cell_id, page.page_id, "cell", lambda item=cell: item.model_dump(mode="json"))
    for block in layout.blocks if detail else ():
        add("content_block", run_id=literal(run_id), block_id=literal(block.block_id),
            payload=js(block.model_dump(mode="json")))
    for aid, kind, path, payload, page_id in artifacts:
        raw = path.read_bytes()
        add("artifact", run_id=literal(run_id), artifact_id=literal(aid), kind=literal(kind),
            sha256=literal(sha256(raw).hexdigest()), local_path=literal(path.resolve()),
            payload=js(payload) if payload_ids is None or aid in payload_ids else "'{}'::jsonb")
        if page_id:
            evidence(aid, page_id, kind, lambda aid=aid, raw=raw: {"artifact_id": aid, "sha256": sha256(raw).hexdigest()})
    for i, rel in enumerate(layout.relations):
        if rel.source not in evidence_ids or rel.target not in evidence_ids:
            raise ValueError("DANGLING_LAYOUT_REFERENCE")
        if detail:
            add("relation_candidate", run_id=literal(run_id), relation_id=literal(str(i)),
                source_ref=literal(rel.source), target_ref=literal(rel.target),
                payload=js(rel.model_dump(mode="json")))
    if fail:
        statements.append("SELECT 1 / 0;")
    statements.append(f"UPDATE staging.processing_run SET status={literal(layout.processing_status)}, "
                      f"completed_at=now() WHERE run_id={literal(run_id)};")
    statements.append("COMMIT;")
    try:
        sql("\n".join(statements))
    except Exception:
        sql(f"UPDATE staging.processing_run SET status='FAILED',error='TRANSACTION_ROLLED_BACK',"
            f"completed_at=now() WHERE run_id={literal(run_id)};")
        raise
    return {"duplicate": False, "run_id": run_id, "database_seconds": perf_counter() - started}
