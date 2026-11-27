"""teams / legacy_review / service."""

from doc_understanding.runtime.telemetry import Trace, load_config
from doc_understanding.runtime import credentials, model_client, paths, settings
from doc_understanding.agents.workflows.legacy_review import graph


def execute(record, data, progress, rid):
    # Only the explicitly enabled local workflow reads the existing credential file.
    if not settings.configured(data)["enabled"]:
        raise ValueError("MODEL_REVIEW_DISABLED")
    key = credentials.model_key()
    trace_config = paths.ROOT / "infra/langfuse/client.env.local"
    try:
        trace = Trace(load_config(trace_config)) if trace_config.exists() else Trace()
    except Exception:
        trace = Trace()
        trace.warnings.append("LANGFUSE_CONFIG_UNAVAILABLE")
    return graph.run_review(
        record,
        data,
        progress,
        rid,
        calls=model_client.ModelCalls(
            data, key=key, daily_limit=settings.configured(data)["daily_limit"]
        ),
        trace=trace,
    )
