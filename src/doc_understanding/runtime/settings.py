"""runtime / settings."""

from pathlib import Path

from doc_understanding.runtime import artifacts


def configured(data):
    path = Path(data) / "review-agent-config.json"
    config = artifacts.read(path) if path.exists() else {}
    return dict(
        enabled=config.get("enabled") is True,
        daily_limit=max(2, min(40, int(config.get("daily_limit", 20)))),
        activated_at=config.get("activated_at", "9999"),
    )
