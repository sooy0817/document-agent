"""이미 적재된 문서의 메타데이터(기관·문서 이름·종류·기준 시점)를 뽑아 저장한다. 다시 적재하지 않는다.

문서마다 모델을 한 번 부른다(앞쪽 두 쪽의 글만 보낸다). 같은 지시문으로 이미 뽑은 문서는 건너뛴다(--again으로 다시).
결과: rag.document_metadata, 호출 기록은 work/metadata/<시각>/<문서>/ 아래, 요약은 같은 폴더의 report.json.
사용: extract_metadata.py [--limit N] [--again] [--show]
  --show: 모델을 부르지 않고 저장된 메타데이터를 표로 보여 준다.
"""
import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from doc_understanding.rag import catalog, repository
from doc_understanding.rag.metadata import extract as extraction
from doc_understanding.rag.metadata import store
from doc_understanding.runtime.structured_call import CallPolicy, Ledger

POLICY = CallPolicy(model="gpt-4.1-mini", input_usd_per_million=0.4, output_usd_per_million=1.6, max_cost_usd=0.03)


def show(rows):
    views = store.view([r["document_id"] for r in rows])
    for r in sorted(rows, key=lambda r: (views[r["document_id"]]["organization"], r["name"])):
        v = views[r["document_id"]]
        aliases = ", ".join(a["text"] + ("" if a["origin"] != "model_unverified" else "?") for a in v["aliases"])
        print(f"{'!' if v['needs_review'] else ' '} {r['pages']:>3}쪽 | {v['organization'] or '-':<18} | {v['title'][:34] or '-':<34} | "
              f"{v['document_type'][:14] or '-':<14} | {v['as_of'][:16] or '-':<16} | {aliases[:40]:<40} | {v['status']:<9} | {r['name'][:40]}")
    print(len(rows), "documents | 기관 없음", sum(not views[r["document_id"]]["organization"] for r in rows),
          "| 이름 없음", sum(not views[r["document_id"]]["title"] for r in rows),
          "| 확인 필요", sum(views[r["document_id"]]["needs_review"] for r in rows))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100000)
    parser.add_argument("--again", action="store_true", help="이미 뽑은 문서도 다시 뽑는다")
    parser.add_argument("--show", action="store_true", help="저장된 메타데이터를 보여 주기만 한다")
    args = parser.parse_args()
    rows = catalog.current_runs()
    if args.show:
        return show(rows)
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("API_KEY_UNAVAILABLE")
    done = set() if args.again else store.extracted_with([r["document_id"] for r in rows], extraction.PROMPT_SHA256)
    todo = [r for r in rows if r["document_id"] not in done][:args.limit]
    out = Path("work/metadata") / f"{datetime.now():%Y%m%d-%H%M%S}"
    out.mkdir(parents=True)
    ledger = Ledger(max_calls=len(todo) + 1, max_total_usd=max(0.05, 0.03 * len(todo)))
    report = []
    for index, row in enumerate(todo, 1):
        record = dict(document_id=row["document_id"], run_id=row["run_id"], name=row["name"])
        try:
            doc = repository.fetch_document(row["run_id"])
            result, call = extraction.extract(doc, POLICY, out / row["run_id"], allow_external=True, ledger=ledger)
            store.put_extracted(row["document_id"], result)
            record.update(status=result["status"], fields={k: v["value"] for k, v in result["fields"].items()},
                          dropped=result["dropped"], cost_usd=(call or {}).get("estimated_cost_usd", 0.0))
        except Exception as error:  # 한 문서 실패는 기록하고 계속한다.
            record.update(status="failed", error=type(error).__name__)
        report.append(record)
        print(f"[{index}/{len(todo)}] {record['status']} | {json.dumps(record.get('fields') or {}, ensure_ascii=False)[:150]} | "
              f"{record.get('dropped') or ''} | {row['name'][:40]}", flush=True)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf8")
    print("documents", len(todo), "| skipped (already extracted)", len(done), "| cost $", round(ledger.estimated_usd, 4),
          "| report", out / "report.json")


if __name__ == "__main__":
    main()
