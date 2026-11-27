"""validation / source_review."""

from doc_understanding.validation import review


def check_source(packet, source):
    if not source:
        return ["BLIND_SOURCE_REVIEW_UNAVAILABLE"]
    fake = {
        "issues": [
            dict(
                issue_id=str(n),
                target_ids=[r["source_id"]] + ([r["target_id"]] if r["target_id"] else []),
                relation="none",
                citations=r["citations"],
            )
            for n, r in enumerate(source["relations"])
        ]
    }
    judge = {
        "assessments": [
            dict(issue_id=i["issue_id"], citations=i["citations"]) for i in fake["issues"]
        ]
    }
    errors = review.check_outputs(packet, fake, judge)
    cells = {e["id"]: e for e in packet["evidence"]}
    for r in source["relations"]:
        a, b = cells.get(r["source_id"], {}), cells.get(r["target_id"], {})
        if r["relation"] == "parent_table" and (
            a.get("kind") not in ("cell", "native_cell")
            or b.get("kind") != "table"
            or a.get("table_id") == r["target_id"]
        ):
            errors.append("INVALID_NESTED_RELATION_ENDPOINTS")
    return errors
