# 역할: 문서 고르기에 쓰는 문서 목록과 '본문에 이 낱말이 나오는가' 조회를 DB에서 읽는다.
"""문서 디렉터리(DB).

문서 고르기(rag.agentic.documents)는 문서 목록과 낱말 조회를 인자로 받는다. 여기서 그 둘을 현재 검색 대상
(목록표의 현재 실행, 지금 색인 버전)에 대해 만든다. 문서 저장본은 열지 않는다: 목록표와 메타데이터, 검색 항목만 읽는다.
지금은 문서 목록 전체를 한 번에 읽는다. 문서가 수만 개가 되면 이름 맞춤을 SQL로 옮겨야 한다(미적용).
"""
from doc_understanding.rag import catalog
from doc_understanding.rag.metadata import store
from doc_understanding.storage.staging.local import query_params


def documents(index_version, run_ids=None):
    """현재 검색 대상 문서들: 목록표의 줄에 메타데이터(기관·문서 이름·다른 표기 등)를 합친 것."""
    current = (catalog.current_runs() if run_ids is None else
               [r for r in catalog.lookup(run_ids).values() if r["is_current"]])
    rows = [r for r in current if r["index_version"] == index_version]
    views = store.view([r["document_id"] for r in rows])
    out = []
    for r in rows:
        v = views[r["document_id"]]
        out.append(dict(run_id=r["run_id"], document_id=r["document_id"], name=r["name"], source_name=r["source_name"],
                        pages=r["pages"], organization=v["organization"], title=v["title"],
                        document_type=v["document_type"], as_of=v["as_of"], aliases=v["aliases"]))
    return out


def mentions(index_version, run_ids=None):
    """낱말이 현재 검색 대상의 어느 검색 항목에라도 나오는가(공백·대소문자 무시). run_id를 주면 그 문서 안에서만."""
    scope = f"e.run_id IN ({catalog.scope_sql(index_version)})" if run_ids is None else "e.run_id = ANY(%s)"

    def said(word, run_id=None):
        where, params = scope, ([] if run_ids is None else [list(run_ids)])
        if run_id:
            where, params = "e.run_id = %s", [run_id]
        rows = query_params("SELECT 1 AS x FROM rag.search_entry e WHERE " + where + " AND position(%s in "
                            "lower(regexp_replace(e.search_text, '[[:space:][:punct:]]+', '', 'g'))) > 0 LIMIT 1", [*params, word])
        return bool(rows)

    return said
