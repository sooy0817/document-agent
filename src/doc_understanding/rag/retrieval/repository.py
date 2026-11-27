# 역할: PostgreSQL에서 후보와 원본 JSON을 조회하고 셀·중첩 문맥을 SQL로 대조한다.
import threading
from collections import OrderedDict

from doc_understanding.domain.pipeline_contracts import CanonicalDocument
from doc_understanding.parser.assembly.document import validate_document
from doc_understanding.storage.documents import ARTIFACT_ID
from doc_understanding.storage.staging.local import literal, query
from doc_understanding.rag.retrieval.lexical import term_forms
from doc_understanding.rag.observability.repository import traced_repository


@traced_repository('nested_source_lookup')
def sql_nested_probe(run_id, parent_cell_id):
    """Restore parent and body from original JSONB, not search projection text."""
    return query(f"""SELECT c AS parent_cell,a.payload->'contents' AS contents
        FROM staging.artifact a,
        LATERAL jsonb_array_elements(a.payload->'document_ir'->'pages') p,
        LATERAL jsonb_array_elements(p->'tables') t,
        LATERAL jsonb_array_elements(t->'cells') c
        WHERE a.run_id={literal(run_id)} AND a.artifact_id={literal(ARTIFACT_ID)}
        AND c->>'cell_id'={literal(parent_cell_id)}""")



# 최근에 읽은 문서 몇 개를 기억한다. 질문 한 건이 같은 문서를 여러 번 읽고(표 목록·구조 일치·근거 복원), 문서를
# 읽어 검증하는 데 0.5초쯤 걸린다. 같은 객체를 돌려주면 문서 객체에 매인 계산(칸 안 내용 등)도 다시 하지 않는다.
# 문서는 고정(frozen) 모델이라 공유해도 바뀌지 않는다. 키에 스냅샷 해시를 넣어, 같은 실행 ID의 내용이 달라지면 다시 읽는다.
DOCUMENT_CACHE_SIZE = 4
_documents = OrderedDict()
_documents_lock = threading.Lock()


@traced_repository('fetch_document')
def fetch_document(run_id):
    snapshot = query(f"SELECT snapshot_sha256,artifact_id FROM rag.document_snapshot WHERE run_id={literal(run_id)}")
    if len(snapshot) != 1:
        raise ValueError('DOCUMENT_SNAPSHOT_NOT_FOUND')
    key = (str(run_id), snapshot[0]['snapshot_sha256'], str(snapshot[0]['artifact_id']))
    with _documents_lock:
        if key in _documents:
            _documents.move_to_end(key)
            return _documents[key]
    rows = query(f"SELECT a.payload FROM staging.artifact a JOIN rag.document_snapshot d "
                 f"ON a.run_id=d.run_id AND a.artifact_id=d.artifact_id WHERE a.run_id={literal(run_id)}")
    if len(rows) != 1:
        raise ValueError('DOCUMENT_SNAPSHOT_NOT_FOUND')
    doc = CanonicalDocument.model_validate(rows[0]['payload'])
    validate_document(doc)
    with _documents_lock:
        _documents[key] = doc
        _documents.move_to_end(key)
        while len(_documents) > DOCUMENT_CACHE_SIZE:
            _documents.popitem(last=False)
    return doc



def table_ids(run_id):
    """이 실행에 저장된 표 근거의 ID 목록(쪽·표 순서). 문서 저장본을 열지 않는다."""
    rows = query(f"SELECT item_id FROM rag.evidence_item WHERE run_id={literal(run_id)} AND kind='table' ORDER BY item_id")
    return [row['item_id'] for row in rows]


def evidence_source(run_id):
    """질문 경로용 근거 출처: 적재 때 저장한 표·본문 항목을 ID로 읽는다(문서 전체를 읽지 않는다).

    저장본이 지금 색인 버전으로 만들어졌을 때만 준다. 아니면 다시 적재해야 한다.
    """
    from doc_understanding.rag.evidence.source import StoredSource
    from doc_understanding.rag.indexing.writer import INDEX_VERSION

    rows = query(f"SELECT document_id, index_version FROM rag.document_snapshot WHERE run_id={literal(run_id)}")
    if len(rows) != 1:
        raise ValueError('DOCUMENT_SNAPSHOT_NOT_FOUND')
    if rows[0]['index_version'] != INDEX_VERSION:
        raise ValueError('EVIDENCE_STORE_STALE')

    def load(kind, ids):
        if not ids:
            return {}
        wanted = ','.join(literal(i) for i in ids)
        if kind == 'entry':
            # 검색 조각 대조용 기준 항목: 색인에 저장된 검색 항목 그대로.
            found = query(f"SELECT entry_id AS item_id, payload FROM rag.search_entry WHERE run_id={literal(run_id)} "
                          f"AND entry_id IN ({wanted})")
        else:
            found = query(f"SELECT item_id, payload FROM rag.evidence_item WHERE run_id={literal(run_id)} "
                          f"AND kind={literal(kind)} AND item_id IN ({wanted})")
        return {row['item_id']: row['payload'] for row in found}

    return StoredSource(dict(document_id=rows[0]['document_id']), load)


def _restriction(run_id=None, run_ids=None):
    if run_ids is not None:
        from doc_understanding.rag.retrieval.scope import run_list
        return 'AND e.run_id IN ('+run_list(run_ids)+')'
    if run_id:
        return f'AND e.run_id={literal(run_id)}'
    return '''AND e.run_id IN (
        SELECT DISTINCT ON (d.document_id) d.run_id FROM rag.document_snapshot d
        JOIN staging.processing_run pr ON pr.run_id=d.run_id
        WHERE pr.status NOT IN ('FAILED','RUNNING') ORDER BY d.document_id,d.created_at DESC,d.run_id DESC)'''


def lexical_sql(question, limit, restriction):
    """키워드 점수 SELECT 문장 하나. 검색어가 없으면 None. 1개 검색과 일괄 검색이 같은 문장을 쓴다."""
    forms = term_forms(question)
    if not forms:
        return None
    pairs = ','.join(f'({literal(term)},{literal(spaced)})' for term, spaced in forms)
    return f"""SELECT e.run_id,e.payload,
        matches.weight::float / {len(forms)}
         + ts_rank_cd(e.search_vector,plainto_tsquery('simple',{literal(question)})) AS score
        FROM rag.search_entry e JOIN staging.processing_run r ON r.run_id=e.run_id
        CROSS JOIN LATERAL (SELECT lower(e.search_text) AS full_text,
            lower(e.payload->>'primary_text') AS primary_text OFFSET 0) txt
        CROSS JOIN LATERAL (
            SELECT sum(least(length(term),6) * CASE
                WHEN strpos(txt.primary_text,term)>0 OR strpos(txt.primary_text,spaced)>0
                THEN 1.0 ELSE .2 END) AS weight
            FROM (VALUES {pairs}) AS q(term,spaced)
            WHERE strpos(txt.full_text,term)>0 OR strpos(txt.full_text,spaced)>0
        ) matches
        WHERE r.status NOT IN ('FAILED','RUNNING') {restriction} AND matches.weight IS NOT NULL
        ORDER BY score DESC,e.run_id,e.entry_id LIMIT {max(1,min(int(limit),30))}"""


@traced_repository('search')
def search(question, run_id=None, limit=8, *, run_ids=None):
    if run_ids is not None and not run_ids:
        return []
    statement = lexical_sql(question, limit, _restriction(run_id, run_ids))
    return query(statement) if statement else []


def search_many(questions, limit=8, *, run_ids=None):
    """여러 검색어의 키워드 검색을 SQL 한 번으로 실행한다. 결과는 검색어별 search()와 같다."""
    if run_ids is not None and not run_ids:
        return [[] for _ in questions]
    restriction = _restriction(None, run_ids)
    parts = [(i, lexical_sql(q, limit, restriction)) for i, q in enumerate(questions)]
    parts = [(i, sql_text) for i, sql_text in parts if sql_text]
    out = [[] for _ in questions]
    if not parts:
        return out
    union = " UNION ALL ".join(f"(SELECT {i} AS query_no, x.* FROM ({sql_text}) x)" for i, sql_text in parts)
    # 하위 쿼리의 순서는 UNION 뒤에 보장되지 않으므로 검색어 번호와 같은 정렬 키로 다시 정렬한다.
    for row in query(f"SELECT * FROM ({union}) u ORDER BY query_no, score DESC, run_id, payload->>'entry_id'"):
        out[row.pop("query_no")].append(row)
    return out



@traced_repository('cell_lookup')
def sql_value_probe(run_id, table_id, cell_id):
    """Actual JSONB SQL join between physical cells and derived table paths."""
    return query(f"""SELECT p->>'table_id' table_id,c->>'cell_id' cell_id,
         c->'original_text' value_raw,m->'row_path' row_node_ids,m->'column_path' column_node_ids
       FROM staging.artifact a,
         LATERAL jsonb_array_elements(a.payload->'physical_tables') p,
         LATERAL jsonb_array_elements(p->'cells') c,
         LATERAL jsonb_array_elements(a.payload->'table_structure') s,
         LATERAL jsonb_array_elements(s->'cell_mappings') m
       WHERE a.run_id={literal(run_id)} AND a.artifact_id={literal(ARTIFACT_ID)}
         AND p->>'table_id'={literal(table_id)} AND s->>'table_id'=p->>'table_id'
         AND c->>'cell_id'={literal(cell_id)} AND m->>'cell_id'=c->>'cell_id'""")
