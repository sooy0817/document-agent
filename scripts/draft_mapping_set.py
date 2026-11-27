"""매핑 테스트셋 초안 생성: 코퍼스 문서의 현재 구조 분석 결과를 기대값 초안으로 옮긴다. 모델 호출 없음.

초안은 '분석기가 낸 답'이므로 그대로는 채점 기준이 될 수 없다(자기 채점). 모든 셀은 reviewed=false로 나오며,
사람이나 원본 대조로 확인·수정한 셀만 검토 표시를 한다(/mapping 화면).

  python scripts/draft_mapping_set.py --name mapping-holdout-v1 --doc hana=<run_id 앞부분> --doc bea=<run_id 앞부분> …
"""
import argparse
from pathlib import Path

from doc_understanding.evaluation.mapping.dataset import (
    CellRef, ExpectedCell, ExpectedTable, MappingSet, compact, content_refs, save)
from doc_understanding.rag import repository
from doc_understanding.rag.evidence.inspect import document_views
from doc_understanding.services.corpus_rag import INDEX_VERSION, latest_runs


def draft_table(alias, view):
    cells = []
    for c in sorted(view["cells"], key=lambda c: (c["r"], c["c"])):
        role = ("ignore" if not c["text"].strip() else "container" if c["container"]
                else "ignore" if c["role"] == "unknown" else c["role"])
        cell = dict(r=c["r"], c=c["c"], text=compact(c["text"]), role=role)
        if role == "value":
            cell.update(row_path=c["row_path"], column_path=c["column_path"])
        if role == "container":
            cell["content"] = content_refs(c["container"]["items"])
        if c["role"] == "unknown" and c["text"].strip():
            cell["note"] = "분석기가 역할을 정하지 못한 칸(초안에서는 채점 제외). 확인 필요"
        cells.append(ExpectedCell(**cell))
    short = "/".join(view["table_id"].split("/")[-2:])
    parents = [CellRef(table="/".join(p["table"].split("/")[-2:]), r=p["r"], c=p["c"]) for p in view["parents"]]
    return ExpectedTable(doc=alias, table=short, parents=parents, cells=cells,
                         patterns=["nested_in_cell"] if parents else [])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--doc", action="append", required=True, help="별칭=실행 ID 앞부분")
    parser.add_argument("--note", default="")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    runs = [r for r in latest_runs() if r["index_version"] == INDEX_VERSION]
    documents, tables = {}, []
    for item in args.doc:
        alias, prefix = item.split("=", 1)
        run = next(r for r in runs if r["run_id"].startswith(prefix))
        doc = repository.fetch_document(run["run_id"])
        documents[alias] = doc.document_id
        views = document_views({doc.document_id: doc})
        tables += [draft_table(alias, views[t.table_id]) for t in doc.physical_tables]
    data = MappingSet(name=args.name, status="draft", reviewed_by_human=False, draft_by="analyzer output (unreviewed)",
                      note=args.note, documents=documents, tables=tables)
    out = args.out or Path("config/evaluation") / f"{args.name}.json"
    save(data, out)
    print(out, len(tables), "tables", sum(len(t.cells) for t in tables), "cells")


if __name__ == "__main__":
    main()
