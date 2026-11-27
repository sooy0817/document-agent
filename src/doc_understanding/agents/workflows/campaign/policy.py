"""Deterministic decisions for the first campaign milestone."""


def next_action(samples):
    eligible = [s for s in samples if s["role"] in ("development", "regression")]
    pending = next((s for s in eligible if s["status"] == "pending"), None)
    if pending:
        return {"next_team": "parse", "sample_id": pending["id"], "reason": "registered_sample_pending"}
    if any(s["status"] in ("running", "unknown") for s in eligible):
        return {"next_team": "finish", "sample_id": "", "reason": "execution_state_unknown"}
    if any(s["status"] == "failed" for s in eligible):
        return {"next_team": "finish", "sample_id": "", "reason": "sample_processing_failed"}
    if not eligible:
        return {"next_team": "finish", "sample_id": "", "reason": "no_development_or_regression_samples"}
    if any(s.get("review_status") in ("running", "failed") for s in eligible):
        return {"next_team": "finish", "sample_id": "", "reason": "local_review_incomplete"}
    unreviewed = next((s for s in eligible if s["status"] == "completed" and not s.get("review_status")), None)
    if unreviewed:
        return {"next_team": "review", "sample_id": unreviewed["id"], "reason": "local_review_pending"}
    return {"next_team": "finish", "sample_id": "", "reason": "specialist_review_not_connected"}
