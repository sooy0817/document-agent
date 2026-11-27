"""Turn bounded development-only triage signals into existing review-queue requests."""
import hashlib
import json
from pathlib import Path

import fitz

from doc_understanding.review import error_basket
from doc_understanding.runtime.review_queue import ReviewQueue


def prepare_requests(store, campaign_id, *, limit=5):
    queue = ReviewQueue(store.root)
    total = 0
    for sample in store.samples(campaign_id):
        if sample["role"] != "development" or sample["review_status"] != "completed":
            continue
        folder = Path(sample["output"])
        try:
            result_data = (folder / "result.json").read_bytes()
            report = json.loads((folder / "campaign-review.json").read_text(encoding="utf8"))
            if hashlib.sha256(result_data).hexdigest() != report["result_sha256"]:
                raise ValueError("RESULT_CHANGED")
            if hashlib.sha256((folder / "input.pdf").read_bytes()).hexdigest() != sample["sha256"]:
                raise ValueError("SOURCE_CHANGED")
            actionable = [f for f in report["findings"] if f.get("table_id") and f.get("code") in
                          ("STRUCTURE_NOT_VALIDATED", "CELL_PATH_UNCERTAIN")]
            if not actionable:
                continue
            with fitz.open(folder / "input.pdf") as pdf:
                pages = len(pdf)
            job = {"id": sample["id"], "name": Path(sample["path"]).name,
                   "pages": pages, "status": "done"}
            seen_tables = set()
            for finding in actionable:
                table = finding.get("table_id")
                if not table or table in seen_tables or finding["code"] not in (
                        "STRUCTURE_NOT_VALIDATED", "CELL_PATH_UNCERTAIN"):
                    continue
                seen_tables.add(table)
                if total >= limit:
                    store.event(campaign_id, "review", "review_request_limit_reached", sample["id"])
                    return total
                if store.has_review_request(sample["id"], finding["page"], table):
                    continue
                request = error_basket.CollectionRequest(page=finding["page"], scope="table",
                    table_id=table, error_type="other",
                    note="자동 점검 신호: " + finding["code"] + ". 오류 확정 전 근거 확인 요청.")
                item = error_basket.collect(store.root / "error-basket", folder, job, request)
                # Only local evidence checks are enabled here; no external model is invoked.
                rid = queue.enqueue(item["id"], mode="local")
                store.add_review_request(sample["id"], finding["page"], table, item["id"], rid)
                total += 1
                # Select only this explicitly local request; do not drain other work.
                queue.run_one(request_id=rid)
            store.event(campaign_id, "review", "review_requests_prepared", sample["id"])
        except Exception:
            store.event(campaign_id, "review", "review_request_preparation_failed", sample["id"])
    return total
