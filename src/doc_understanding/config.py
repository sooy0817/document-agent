import os
from pathlib import Path

from .domain.contracts import ExtractionLimits


def artifact_root() -> Path:
    return Path(os.environ.get("NH_ARTIFACT_ROOT", "data/runs"))


def extraction_limits() -> ExtractionLimits:
    variables = {"max_bytes": "NH_MAX_BYTES", "max_pages": "NH_MAX_PAGES",
                 "render_dpi": "NH_RENDER_DPI", "max_pixels": "NH_MAX_PIXELS"}
    return ExtractionLimits(**{k: int(os.environ[v]) for k, v in variables.items() if v in os.environ})
