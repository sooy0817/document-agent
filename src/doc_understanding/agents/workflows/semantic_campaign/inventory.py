"""Geometry-based sampling signals, not approved semantic classifications."""

import hashlib
import json
from pathlib import Path
from typing import Literal

from doc_understanding.domain.models import EvidenceModel

TYPES = (
    "simple",
    "multi_header",
    "row_groups",
    "variable_columns",
    "nested",
    "continuation",
    "narrative",
    "non_table_candidate",
)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class TableProfile(EvidenceModel):
    table_id: str
    job_id: str
    document_sha256: str
    page: int
    tags: tuple[str, ...]
    signals: dict[str, tuple[str, ...]]
    shape_sha256: str
    physical_issues: tuple[str, ...]
    family_status: Literal["unconfirmed"] = "unconfirmed"
    semantic_verified: Literal[False] = False


def profile(raw, job_id):
    structures = {s["table_id"]: s for s in raw["table_structure"]}
    tables = raw["physical_tables"]
    nested = set()
    for a in tables:
        for b in tables:
            if a["page_number"] != b["page_number"] or a["table_id"] == b["table_id"]:
                continue
            x, y = a["bounding_box"], b["bounding_box"]
            if (
                x != y
                and x["x0"] <= y["x0"]
                and x["y0"] <= y["y0"]
                and x["x1"] >= y["x1"]
                and x["y1"] >= y["y1"]
            ):
                nested.update((a["table_id"], b["table_id"]))
    continued = {
        tid
        for link in raw.get("logical_table_links", [])
        if link["kind"] == "page_continuation"
        for tid in (link["source_table_id"], link["target_table_id"])
    }
    result = []
    for t in tables:
        cells = t["cells"]
        st = structures.get(t["table_id"], {})
        signals = {}
        spans = [
            (c.get("row_index"), c.get("column_index"), c.get("rowspan"), c.get("colspan"))
            for c in cells
        ]
        merged = [
            c["cell_id"]
            for c in cells
            if (c.get("rowspan") or 1) > 1 or (c.get("colspan") or 1) > 1
        ]
        if not merged and len(t["rows"]) > 1 and len(t["columns"]) > 1:
            signals["simple"] = tuple(c["cell_id"] for c in cells[:2])
        if any(p["header_rows"] > 1 for p in st.get("panels", [])):
            signals["multi_header"] = tuple(n["source_cell_id"] for n in st.get("column_tree", []))
        groups = [
            c["cell_id"]
            for c in cells
            if (c.get("rowspan") or 1) > 1 and c.get("column_index") == 0
        ]
        if groups:
            signals["row_groups"] = tuple(groups)
        if any((c.get("colspan") or 1) > 1 and (c.get("row_index") or 0) > 0 for c in cells):
            signals["variable_columns"] = tuple(merged)
        if t["table_id"] in nested:
            signals["nested"] = (t["table_id"],)
        if t["table_id"] in continued:
            signals["continuation"] = (t["table_id"],)
        prose = tuple(c["cell_id"] for c in cells if len(c.get("original_text") or "") > 160)
        if prose:
            signals["narrative"] = prose
        if len(t["rows"]) <= 1 or len(t["columns"]) <= 1:
            signals["non_table_candidate"] = (t["table_id"],)
        issues = []
        if any(None in s for s in spans):
            issues.append("UNKNOWN_GRID_SLOT")
        if any(c.get("status") == "unresolved" for c in cells):
            issues.append("UNRESOLVED_PHYSICAL_CELL")
        ids = [c["cell_id"] for c in cells]
        if len(ids) != len(set(ids)):
            issues.append("DUPLICATE_CELL_ID")
        result.append(
            TableProfile(
                table_id=t["table_id"],
                job_id=job_id,
                document_sha256=raw["document_ir"]["source_sha256"],
                page=t["page_number"],
                tags=tuple(signals),
                signals=signals,
                shape_sha256=digest({"slots": sorted(spans, key=str), "tags": sorted(signals)}),
                physical_issues=tuple(issues),
            ).model_dump(mode="json")
        )
    return result


def select(profiles, per_type=3, maximum=16):
    """Prefer clean, diverse shapes/documents. Exact shape is not a template family."""
    if not 1 <= per_type <= 10 or not 1 <= maximum <= 40:
        raise ValueError("INVALID_SELECTION_BUDGET")
    selected, shapes, counts = [], set(), dict.fromkeys(TYPES, 0)
    pending = [p for p in profiles if not p["physical_issues"]]
    while pending and len(selected) < maximum:
        documents = {p["document_sha256"] for p in selected}
        pending.sort(
            key=lambda p: (
                -sum(counts[k] < per_type for k in p["tags"]),
                p["shape_sha256"] in shapes,
                p["document_sha256"] in documents,
                p["document_sha256"],
                p["page"],
                p["table_id"],
            )
        )
        p = pending.pop(0)
        if p["shape_sha256"] in shapes or not any(counts[k] < per_type for k in p["tags"]):
            continue
        selected.append(p)
        shapes.add(p["shape_sha256"])
        for k in p["tags"]:
            counts[k] += 1
    return {
        "selected": selected,
        "coverage": counts,
        "gaps": {k: max(0, per_type - v) for k, v in counts.items() if v < per_type},
        "physical_triage": [p for p in profiles if p["physical_issues"]],
        "family_verified": False,
        "holdout_evaluated": False,
    }
