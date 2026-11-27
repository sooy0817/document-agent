"""teams / evidence_review / service."""

from pathlib import Path

from doc_understanding.runtime import telemetry
from doc_understanding.runtime import budget as runtime_budget
from doc_understanding.runtime import credentials, model_client, paths, settings
from doc_understanding.agents.workflows.evidence_review import graph, profiles, version


def execute(record, data, progress, rid):
    if not settings.configured(data)["enabled"]:
        raise ValueError("MODEL_REVIEW_DISABLED")
    # A fresh review needs at least source observation, analysis and comparison.
    # Do not spend the day's last call on an orphaned first stage.
    import sqlite3
    from datetime import datetime, timedelta, timezone

    budget = Path(data) / "review-queue/agent-budget.sqlite3"
    saved = Path(data) / "review-queue/agents-v2" / rid
    needed = sum(
        not (saved / (role + ".json")).exists()
        for role in ("source_review", "analyst_v2", "judge_v2")
    )
    if budget.exists():
        with sqlite3.connect(budget) as connection:
            used = connection.execute(
                "SELECT count(*) FROM calls WHERE day=?",
                (datetime.now(timezone(timedelta(hours=9))).date().isoformat(),),
            ).fetchone()[0]
        if used + needed > settings.configured(data)["daily_limit"]:
            raise runtime_budget.DailyReviewBudgetExceeded()
    # Reuse the same explicit credential source and transport as v1.

    key = credentials.model_key()
    try:
        trace = telemetry.Trace(
            telemetry.load_config(paths.ROOT / "infra/langfuse/client.env.local")
        )
    except Exception:
        trace = telemetry.Trace()
        trace.warnings.append("LANGFUSE_CONFIG_UNAVAILABLE")
    calls = model_client.ModelCalls(
        data,
        key=key,
        daily_limit=settings.configured(data)["daily_limit"],
        profiles=profiles.PROFILES,
        version=version.VERSION,
    )
    return graph.run_review(record, data, progress, rid, calls=calls, trace=trace)
