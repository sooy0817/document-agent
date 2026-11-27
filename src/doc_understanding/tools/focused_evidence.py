"""tools / focused_evidence."""

import hashlib
import json
from pathlib import Path

import fitz

from doc_understanding.runtime import artifacts
from doc_understanding.tools import relation_index

MAX_CELLS = 80


MAX_TARGETS = 12


def overlap(a, b, axis):
    lo, hi = axis + "0", axis + "1"
    return max(0, min(a[hi], b[hi]) - max(a[lo], b[lo])) / max(
        0.001, min(a[hi] - a[lo], b[hi] - b[lo])
    )


def contains(a, b):
    return a["x0"] <= b["x0"] and a["y0"] <= b["y0"] and a["x1"] >= b["x1"] and a["y1"] >= b["y1"]


def prepare(record, data, *, target_ids=None, context_source_ids=()):
    """Select evidence by geometry and stored references, never by document name or Gold."""
    snap = record["snapshot"]
    folder = Path(data) / snap["job_id"]
    for filename, key in [
        ("document.json", "canonical_sha256"),
        ("input.pdf", "source_pdf_sha256"),
    ]:
        if hashlib.sha256((folder / filename).read_bytes()).hexdigest() != snap[key]:
            raise ValueError("SOURCE_CHANGED")
    doc = artifacts.read(folder / "document.json")
    nested = (
        artifacts.read(folder / "nested.json")
        if (folder / "nested.json").exists()
        else {"cells": []}
    )
    index = relation_index.RelationIndex(doc, nested)
    tables = {t["table_id"]: t for t in doc["physical_tables"]}
    selection = snap["selection"]
    selected = tables.get(selection.get("table_id"))
    if not selected:
        raise ValueError("SELECTED_TABLE_NOT_FOUND")
    selected_ids = (
        [selection["cell_id"]]
        if selection.get("cell_id")
        else [c["cell_id"] for c in selected["cells"]]
    )
    narrowed = target_ids is not None
    if narrowed and (not target_ids or len(target_ids) > MAX_TARGETS
                     or len(target_ids) != len(set(target_ids))
                     or set(target_ids) - set(selected_ids)):
        raise ValueError('INVALID_FOCUSED_TARGETS')
    target_ids = list(target_ids) if narrowed else selected_ids
    if any(s not in index.sources for s in target_ids):
        raise ValueError("SELECTED_CELL_NOT_FOUND")
    reasons = {selected["table_id"]: ["selected_table"]}
    for sid in context_source_ids:
        source = index.sources.get(sid)
        if not source or source.get('table_id') not in tables:
            raise ValueError('REQUIRED_CONTEXT_MISSING')
        reasons.setdefault(source['table_id'], []).append('rule_path_context_candidate')
    extra_cells = set()
    # Stored ancestors are context candidates, not semantic truth; their roles stay hidden.
    pending = [selected["table_id"]]
    visited = set()
    while pending:
        tid = pending.pop()
        if tid in visited:
            continue
        visited.add(tid)
        for edge in index.edges:
            if edge["relation"] == "parent_table" and edge["target_id"] == tid:
                sid = edge["source_id"]
                parent = index.sources.get(sid, {}).get("table_id")
                if parent in tables:
                    extra_cells.add(sid)
                    reasons.setdefault(parent, []).append("stored_parent_context_candidate")
                    pending.append(parent)
            if (
                edge["relation"] == "continuation"
                and edge["target_id"] == tid
                and edge["source_id"] in tables
            ):
                reasons.setdefault(edge["source_id"], []).append(
                    "stored_previous_fragment_candidate"
                )
        # Include containing regions even when a stored parent link is missing.
        box = tables[tid]["bounding_box"]
        containers = [
            t
            for t in tables.values()
            if t["page_number"] == tables[tid]["page_number"]
            and t["table_id"] != tid
            and contains(t["bounding_box"], box)
            and t["bounding_box"] != box
        ]
        if containers:
            parent = min(
                containers,
                key=lambda t: (
                    (t["bounding_box"]["x1"] - t["bounding_box"]["x0"])
                    * (t["bounding_box"]["y1"] - t["bounding_box"]["y0"])
                ),
            )
            reasons.setdefault(parent["table_id"], []).append("geometric_container_candidate")
            extra_cells.update(
                c["cell_id"] for c in parent["cells"] if contains(c["source_bbox"], box)
            )
            pending.append(parent["table_id"])
    # A detached header is possible: include the nearest aligned fragment above.
    box = selected["bounding_box"]
    preceding = [
        t
        for t in tables.values()
        if t["page_number"] == selected["page_number"]
        and t["bounding_box"]["y1"] <= box["y0"]
        and overlap(t["bounding_box"], box, "x") >= 0.8
    ]
    if preceding:
        previous = max(preceding, key=lambda t: t["bounding_box"]["y1"])
        if box["y0"] - previous["bounding_box"]["y1"] <= max(12, min(36, box["y1"] - box["y0"])):
            reasons.setdefault(previous["table_id"], []).append(
                "aligned_previous_fragment_candidate"
            )
    if selection["scope"] != "cell":
        for t in tables.values():
            if (
                t["table_id"] != selected["table_id"]
                and t["page_number"] == selected["page_number"]
                and contains(box, t["bounding_box"])
            ):
                reasons.setdefault(t["table_id"], []).append("geometric_inner_table_candidate")

    evidence = []
    omitted = []
    seen = set()

    def add(sid, kind, text, **attrs):
        if sid in seen:
            return
        seen.add(sid)
        if len(text) > 14000 or len(evidence) >= MAX_CELLS:
            omitted.append(sid)
            return
        evidence.append(dict(id=sid, kind=kind, text=text, **attrs))

    for tid in reasons:
        t = tables[tid]
        add(tid, "table", "", page=t["page_number"], bbox=t["bounding_box"])
        cs = t["cells"]
        if narrowed:
            targets = [c for c in selected['cells'] if c['cell_id'] in target_ids]
            cs = [c for c in cs if c['cell_id'] in target_ids or c['cell_id'] in context_source_ids
                  or tid != selected['table_id']
                  or any(overlap(c['source_bbox'], x['source_bbox'], 'y') >= 0.5
                         or (c['source_bbox']['y1'] <= x['source_bbox']['y0'] + 1.5
                             and overlap(c['source_bbox'], x['source_bbox'], 'x') >= 0.5)
                         for x in targets)]
        if tid != selected["table_id"] and all(
            "parent" in s or "container" in s for s in reasons[tid]
        ):
            parents = [c for c in cs if c["cell_id"] in extra_cells]
            cs = [
                c
                for c in cs
                if c in parents
                or any(overlap(c["source_bbox"], p["source_bbox"], "y") >= 0.5 for p in parents)
            ]
        for c in sorted(cs, key=lambda c: c["cell_id"] not in target_ids):
            add(
                c["cell_id"],
                "cell",
                c.get("original_text") or "",
                table_id=tid,
                page=t["page_number"],
                bbox=c["source_bbox"],
                row=c.get("row_index"),
                column=c.get("column_index"),
                rowspan=c.get("rowspan"),
                colspan=c.get("colspan"),
            )
    # Preserve nearby source spans as context, but never declare their applicability.
    kept_boxes = [e["bbox"] for e in evidence if e["kind"] == "cell"]
    candidates = []
    for page in doc.get("document_ir", {}).get("pages", []):
        if page["page_number"] != selected["page_number"]:
            continue
        for block in page.get("blocks", []):
            for s in block.get("spans", []):
                b = s.get("bbox") or s.get("source", {}).get("bbox")
                if not b or any(contains(cb, b) for cb in kept_boxes):
                    continue
                gap = min(abs(b["y1"] - box["y0"]), abs(b["y0"] - box["y1"]))
                if overlap(b, box, "x") > 0 and gap <= 36:
                    candidates.append((gap, s, b))
    for _, s, b in sorted(candidates, key=lambda v: v[0])[:12]:
        add(s["span_id"], "span", s["text_raw"], page=selected["page_number"], bbox=b)
    focused_tables = set(reasons)
    excluded = [
        t["table_id"]
        for t in tables.values()
        if t["page_number"] == selected["page_number"] and t["table_id"] not in focused_tables
    ]
    packet = dict(
        selection=selection,
        target_ids=target_ids[:MAX_TARGETS],
        unreviewed_target_ids=[] if narrowed else target_ids[MAX_TARGETS:],
        out_of_batch_target_ids=[s for s in selected_ids if s not in target_ids] if narrowed else [],
        evidence=evidence,
        omitted_ids=omitted,
        scope="selected targets and candidate context only; not whole table/document approval",
        context_limitations=[
            "Context selection is heuristic; omitted pages may contain applicable explanations."
        ],
        excluded_table_ids=excluded,
    )
    if len(json.dumps(packet, ensure_ascii=False).encode()) > 100000:
        raise ValueError("FOCUSED_INPUT_BUDGET_EXCEEDED")
    images = []
    with fitz.open(folder / "input.pdf") as pdf:
        for pn in sorted({e["page"] for e in evidence}):
            bs = [e["bbox"] for e in evidence if e["page"] == pn]
            crop = (
                fitz.Rect(
                    min(b["x0"] for b in bs) - 8,
                    min(b["y0"] for b in bs) - 8,
                    max(b["x1"] for b in bs) + 8,
                    max(b["y1"] for b in bs) + 8,
                )
                & pdf[pn - 1].rect
            )
            images.append(
                dict(
                    source_id=doc["document_id"] + f"/focused-page-{pn}",
                    bytes=pdf[pn - 1]
                    .get_pixmap(matrix=fitz.Matrix(2, 2), clip=crop)
                    .tobytes("png"),
                )
            )
    audit = dict(
        selection_reasons=reasons,
        excluded_table_ids=excluded,
        nearby_span_candidates=len(candidates),
        nearby_span_included=min(12, len(candidates)),
        context_is_exhaustive=False,
    )
    return packet, images, index, audit
