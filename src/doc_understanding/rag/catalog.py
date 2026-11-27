# 역할: 문서 목록표(rag.document_catalog). 문서 이름·쪽수·검색 항목 수·현재 검색 대상 실행을 문서 전체를 열지 않고 조회한다.
"""문서 목록표.

색인된 실행마다 한 줄이다. 문서 이름·쪽수 같은 요약은 색인을 만들 때 문서에서 한 번 옮겨 적고, 그 뒤 목록 조회·검색 범위
지정·질문 처리는 이 표만 읽는다(수 MB인 문서 저장본을 열지 않는다).

- is_current: 같은 문서의 실행 중 검색에 쓰는 최신 실행(실패·실행 중 제외, 최근 색인 순). 색인을 만들 때 그 문서만 다시 정한다.
- 색인 버전과 색인 시각은 rag.document_snapshot이 기준이다(여기에 복사하지 않는다).
- 이 표가 생기기 전에 색인된 실행은 ensure()가 한 번 채운다(이때만 문서 저장본을 읽는다).

목록표의 값은 문서에서 옮긴 요약이며 사람 검증을 거친 사실이 아니다.
"""
import threading

from doc_understanding.storage.staging.local import literal, query, query_params, sql

TABLE = """CREATE TABLE IF NOT EXISTS rag.document_catalog (
 run_id text PRIMARY KEY REFERENCES rag.document_snapshot,
 document_id text NOT NULL,
 source_name text,
 display_name text,
 source_media_type text,
 pages integer NOT NULL,
 entries integer NOT NULL,
 tables integer NOT NULL,
 structure_review boolean NOT NULL,
 is_current boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS rag_catalog_document ON rag.document_catalog(document_id);
CREATE INDEX IF NOT EXISTS rag_catalog_current ON rag.document_catalog(run_id) WHERE is_current;"""

# 실패·실행 중이 아닌 실행 가운데 문서마다 가장 최근에 색인된 것(동률이면 run_id가 큰 것).
LATEST = """SELECT DISTINCT ON (s.document_id) s.document_id, s.run_id FROM rag.document_snapshot s
  JOIN staging.processing_run r ON r.run_id=s.run_id
  WHERE r.status NOT IN ('FAILED','RUNNING') {only}
  ORDER BY s.document_id, s.created_at DESC, s.run_id DESC"""

BACKFILL = """INSERT INTO rag.document_catalog(run_id,document_id,source_name,source_media_type,pages,entries,tables,structure_review)
SELECT s.run_id, s.document_id, a.payload->'document_ir'->>'source_name', a.payload->'document_ir'->'source'->>'media_type',
  jsonb_array_length(a.payload->'document_ir'->'pages'),
  (SELECT count(*) FROM rag.search_entry e WHERE e.run_id=s.run_id),
  jsonb_array_length(coalesce(a.payload->'table_structure','[]'::jsonb)),
  jsonb_path_exists(a.payload, '$.issues[*] ? (@.code == "STRUCTURE_REVIEW_CANDIDATE")')
FROM rag.document_snapshot s JOIN staging.artifact a ON a.run_id=s.run_id AND a.artifact_id=s.artifact_id
WHERE NOT EXISTS (SELECT 1 FROM rag.document_catalog c WHERE c.run_id=s.run_id);"""

COLUMNS = """c.run_id, c.document_id, s.index_version, s.created_at::text AS created_at, c.entries, c.pages, c.tables,
  c.source_name, coalesce(c.display_name, c.source_name) AS name, c.source_media_type, c.structure_review"""

_ready = threading.Event()
_lock = threading.Lock()


def refresh_statement(document_id=None):
    """is_current를 다시 정하는 문장. document_id가 있으면 그 문서의 실행만."""
    only = f"AND s.document_id={literal(document_id)}" if document_id is not None else ""
    return (f"UPDATE rag.document_catalog c SET is_current=(c.run_id=l.run_id) FROM ({LATEST.format(only=only)}) l "
            "WHERE l.document_id=c.document_id AND c.is_current IS DISTINCT FROM (c.run_id=l.run_id);")


def ensure():
    """표를 만들고, 목록표에 없는 색인 실행을 채운다. 프로세스마다 한 번만 실행한다."""
    if _ready.is_set():
        return
    with _lock:
        if _ready.is_set():
            return
        sql("BEGIN;\n" + TABLE + "\n" + BACKFILL + "\n" + refresh_statement() + "\nCOMMIT;")
        _ready.set()


def index_statements(run_id, doc, entries):
    """색인 트랜잭션에 함께 넣을 문장: 이 실행의 줄을 쓰고 그 문서의 현재 실행을 다시 정한다."""
    review = any(issue.code == "STRUCTURE_REVIEW_CANDIDATE" for issue in doc.issues)
    values = ",".join([literal(run_id), literal(doc.document_id), literal(doc.document_ir.source_name),
                       literal(doc.document_ir.source.media_type), str(len(doc.document_ir.pages)), str(int(entries)),
                       str(len(doc.table_structure)), "true" if review else "false"])
    # 같은 문서의 두 실행이 동시에 색인되면 서로의 새 줄을 못 본 채 둘 다 현재 실행이 될 수 있다.
    # 문서별 잠금(트랜잭션이 끝나면 풀림)으로 현재 실행 선정을 차례로 하게 한다.
    lock = f"SELECT pg_advisory_xact_lock(hashtextextended({literal(doc.document_id)},0));"
    return [TABLE, lock,
            "INSERT INTO rag.document_catalog(run_id,document_id,source_name,source_media_type,pages,entries,tables,"
            f"structure_review) VALUES ({values}) ON CONFLICT (run_id) DO UPDATE SET source_name=EXCLUDED.source_name,"
            "source_media_type=EXCLUDED.source_media_type,pages=EXCLUDED.pages,entries=EXCLUDED.entries,"
            "tables=EXCLUDED.tables,structure_review=EXCLUDED.structure_review;",
            refresh_statement(doc.document_id)]


def scope_sql(index_version):
    """현재 색인 버전의 검색 대상 실행 ID를 고르는 하위 질의(실행 ID를 나열하지 않는다)."""
    return ("SELECT c.run_id FROM rag.document_catalog c JOIN rag.document_snapshot s ON s.run_id=c.run_id "
            f"WHERE c.is_current AND s.index_version={literal(index_version)}")


def current_runs():
    """문서마다 현재 실행 한 줄(색인 버전과 무관). 문서 저장본을 열지 않는다."""
    ensure()
    return query(f"SELECT {COLUMNS} FROM rag.document_catalog c JOIN rag.document_snapshot s ON s.run_id=c.run_id "
                 "WHERE c.is_current ORDER BY c.document_id")


def stats(index_version):
    """규모 측정용 요약: 현재 색인 버전의 문서·쪽·표·검색 항목·벡터 수와 예전 색인 버전에 머문 문서 수."""
    ensure()
    version = literal(index_version)
    row = query(f"""SELECT count(*) FILTER (WHERE s.index_version={version}) AS documents,
        coalesce(sum(c.pages) FILTER (WHERE s.index_version={version}),0) AS pages,
        coalesce(sum(c.tables) FILTER (WHERE s.index_version={version}),0) AS tables,
        coalesce(sum(c.entries) FILTER (WHERE s.index_version={version}),0) AS entries,
        count(*) FILTER (WHERE s.index_version<>{version}) AS stale_documents
      FROM rag.document_catalog c JOIN rag.document_snapshot s ON s.run_id=c.run_id WHERE c.is_current""")[0]
    row["vectors"] = query("SELECT count(*) n FROM rag.entry_embedding v WHERE v.run_id IN ("
                           + scope_sql(index_version) + ")")[0]["n"] if _has_embeddings() else 0
    return row


def _has_embeddings():
    return bool(query("SELECT 1 AS x FROM information_schema.tables WHERE table_schema='rag' "
                      "AND table_name='entry_embedding'"))


def find(index_version, text=None, limit=20, offset=0, stale=False):
    """현재 문서를 이름으로 찾아 한 쪽 분량만 돌려준다(화면의 선택적 문서 필터용). stale=True면 예전 색인 버전 문서."""
    ensure()
    limit, offset = max(1, min(int(limit), 100)), max(0, int(offset))
    compare = "<>" if stale else "="
    where = f"c.is_current AND s.index_version{compare}%s"
    params = [index_version]
    if text and text.strip():
        where += " AND position(lower(%s) in lower(coalesce(c.display_name,'')||' '||coalesce(c.source_name,'')))>0"
        params.append(text.strip())
    base = f"FROM rag.document_catalog c JOIN rag.document_snapshot s ON s.run_id=c.run_id WHERE {where}"
    total = query_params(f"SELECT count(*) n {base}", params)[0]["n"]
    rows = query_params(f"SELECT {COLUMNS} {base} ORDER BY coalesce(c.display_name, c.source_name), c.run_id "
                        f"LIMIT {limit} OFFSET {offset}", params)
    return dict(total=total, limit=limit, offset=offset, documents=rows)


def set_names(names):
    """화면에서 올린 문서의 표시 이름(업로드한 파일 이름)을 적는다. run_id → 이름. 바뀐 줄만 쓴다."""
    names = {r: n for r, n in names.items() if r and n}
    if not names:
        return
    ensure()
    values = ",".join(f"({literal(r)},{literal(n)})" for r, n in names.items())
    sql(f"UPDATE rag.document_catalog c SET display_name=v.name FROM (VALUES {values}) v(run_id,name) "
        "WHERE c.run_id=v.run_id AND c.display_name IS DISTINCT FROM v.name;")


def current_for_documents(document_ids, index_version):
    """문서 ID 몇 개의 현재 실행(현재 색인 버전인 것만). document_id → run_id."""
    ids = list(dict.fromkeys(d for d in document_ids if d))
    if not ids:
        return {}
    ensure()
    rows = query_params("SELECT c.document_id, c.run_id FROM rag.document_catalog c "
                        "JOIN rag.document_snapshot s ON s.run_id=c.run_id "
                        "WHERE c.is_current AND s.index_version=%s AND c.document_id = ANY(%s)", [index_version, ids])
    return {r["document_id"]: r["run_id"] for r in rows}


def lookup(run_ids):
    """실행 ID 몇 개의 목록표 줄(답변 근거에 나온 문서의 이름을 붙일 때). run_id → 줄."""
    ids = list(dict.fromkeys(r for r in run_ids if r))
    if not ids:
        return {}
    ensure()
    rows = query_params(f"SELECT {COLUMNS}, c.is_current FROM rag.document_catalog c "
                        "JOIN rag.document_snapshot s ON s.run_id=c.run_id WHERE c.run_id = ANY(%s)", [ids])
    return {r["run_id"]: r for r in rows}
