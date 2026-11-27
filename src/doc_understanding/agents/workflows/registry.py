"""Queue mode to team mapping. Loading the registry never activates a model."""

from importlib import import_module

TEAM_SERVICES = {
    "agents_roles": "doc_understanding.agents.workflows.table_review.service",
    "agents_v2": "doc_understanding.agents.workflows.evidence_review.service",
    "agents": "doc_understanding.agents.workflows.legacy_review.service",
}


def get_executor(mode: str):
    """Retain persisted mode names so pending requests and history keep their meaning."""
    try:
        module = TEAM_SERVICES[mode]
    except KeyError:
        raise ValueError("UNKNOWN_REVIEW_TEAM") from None
    return import_module(module).execute
