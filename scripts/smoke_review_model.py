"""Run one bounded live review on the project's synthetic horizontal-table fixture.

Example: python scripts/smoke_review_model.py --data <new-empty-directory>
The specified directory must not already exist. No real user PDF is used.
"""
import argparse
import json
from pathlib import Path

from doc_understanding.review import error_basket
from doc_understanding.runtime.review_queue import ReviewQueue
from doc_understanding.services.documents import worker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()
    data = args.data.resolve()
    data.mkdir(parents=True, exist_ok=False)
    (data / "review-agent-config.json").write_text(json.dumps({
        "enabled": True, "daily_limit": 3, "model": "gpt-4.1-mini-2025-04-14",
    }), encoding="utf8")
    ident = "1" * 32
    output = data / ident
    output.mkdir()
    source = Path("reports/physical-fixtures/horizontal.pdf")
    (output / "input.pdf").write_bytes(source.read_bytes())
    worker(output / "input.pdf", output)
    result = json.loads((output / "result.json").read_text(encoding="utf8"))
    if not result["tables"]:
        raise ValueError("SYNTHETIC_TABLE_NOT_FOUND")
    table = result["tables"][0]
    request = error_basket.CollectionRequest(page=table["page"], scope="table",
                                             table_id=table["id"], error_type="other",
                                             note="Synthetic fixture model review smoke test")
    job = {"id": ident, "name": "synthetic-horizontal.pdf", "status": "done",
           "pages": len(result["pages"])}
    item = error_basket.collect(data / "error-basket", output, job, request)
    queue = ReviewQueue(data)
    rid = queue.enqueue(item["id"], mode="agents_roles")
    queue.run_one(request_id=rid)
    detail = queue.detail(rid)
    roles = data / "review-queue" / "roles-loop" / rid
    calls = []
    for file in sorted(roles.glob("*.json")):
        if file.name not in ("classify_roles.json", "infer_relations.json", "judge_relations.json"):
            continue
        record = json.loads(file.read_text(encoding="utf8"))
        calls.append({"role": file.stem, "status": record.get("status"),
                      "error": record.get("error"), "api_calls": record.get("api_calls")})
    print(json.dumps({"request_status": detail["status"], "queue_error": detail.get("error"),
                      "calls": calls, "output": str(data)}, ensure_ascii=False))
    if detail["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
