"""근거 저장본 점검: DB에 저장된 표·본문 항목이 지금 문서에서 계산한 것과 완전히 같은지 코퍼스 전체에서 비교한다.

질문 경로는 저장본만 읽는다. 저장본이 문서와 다르면 답변 근거가 달라지므로, 색인 코드를 바꾼 뒤와 재색인 뒤에 돌린다.
모델 호출 없음. 다른 문서가 하나라도 있으면 종료 코드 1.
"""
import sys

from doc_understanding.rag import repository
from doc_understanding.rag.evidence.source import CONTENT, TABLE, evidence_items
from doc_understanding.rag.retrieval.repository import evidence_source
from doc_understanding.services.corpus_rag import INDEX_VERSION, latest_runs
from doc_understanding.storage.staging.local import literal, query


def compare(run_id):
    doc = repository.fetch_document(run_id)
    expected = evidence_items(doc)
    stored_count = query(f"SELECT count(*) n FROM rag.evidence_item WHERE run_id={literal(run_id)}")[0]["n"]
    source = evidence_source(run_id)
    source.preload([i for kind, i, _ in expected if kind == TABLE], [i for kind, i, _ in expected if kind == CONTENT])
    different = []
    for kind, item_id, payload in expected:
        found = source.table(item_id) if kind == TABLE else source.content(item_id)
        if found != payload:
            different.append((kind, item_id))
    return dict(expected=len(expected), stored=stored_count, different=different)


def main():
    runs = [r for r in latest_runs() if r["index_version"] == INDEX_VERSION]
    stale = [r for r in latest_runs() if r["index_version"] != INDEX_VERSION]
    failed = 0
    for run in runs:
        result = compare(run["run_id"])
        ok = not result["different"] and result["expected"] == result["stored"]
        failed += not ok
        print(run["run_id"][:8], "items", result["stored"], "/", result["expected"],
              "same" if ok else f"DIFFERENT {result['different'][:3]}", flush=True)
    print(f"checked {len(runs)} documents at {INDEX_VERSION}; {failed} differ; "
          f"{len(stale)} documents are on an older index version (need re-ingest to be searchable)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
