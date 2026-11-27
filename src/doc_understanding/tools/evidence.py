"""tools / evidence."""

import hashlib
import json
from pathlib import Path

import fitz

from doc_understanding.runtime import artifacts
from doc_understanding.tools import evidence_context


def collect_evidence(record, data):
    """Existing CanonicalDocument + parent closure; bounded nearby-page evidence."""
    snap = record["snapshot"]
    folder = Path(data) / snap["job_id"]
    for name, key in [("input.pdf", "source_pdf_sha256"), ("document.json", "canonical_sha256")]:
        if hashlib.sha256((folder / name).read_bytes()).hexdigest() != snap[key]:
            raise ValueError("SOURCE_CHANGED")
    doc = artifacts.read(folder / "document.json")
    page = snap["selection"]["page"]
    selected = {t["table_id"] for t in snap.get("physical_tables", [])}
    nested = (
        artifacts.read(folder / "nested.json")
        if (folder / "nested.json").exists()
        else {"cells": []}
    )
    closure = evidence_context.context_evidence(doc, nested, selected)
    wanted = selected | {t["table_id"] for t in closure["related_physical_tables"]}
    tables = sorted(
        doc["physical_tables"],
        key=lambda t: (
            t["table_id"] not in selected,
            t["table_id"] not in wanted,
            abs(t["page_number"] - page),
        ),
    )
    evidence, omitted, seen = [], [], set()

    def add(source_id, kind, text, **extra):
        if source_id in seen:
            return
        seen.add(source_id)
        # Source text is retained intact or explicitly excluded, never silently shortened.
        if len(text) > 14000:
            omitted.append(source_id)
            return
        evidence.append(dict(id=source_id, kind=kind, text=text, **extra))

    structs = {s["table_id"]: s for s in doc["table_structure"]}
    candidates = []
    for t in tables:
        if t["table_id"] not in wanted and t["page_number"] != page:
            continue
        if len(evidence) + len(t["cells"]) > 160:
            omitted.append(t["table_id"])
            continue
        add(t["table_id"], "table", "", page=t["page_number"], bbox=t["bounding_box"])
        for c in t["cells"]:
            add(
                c["cell_id"],
                "cell",
                c.get("original_text") or "",
                page=t["page_number"],
                table_id=t["table_id"],
                bbox=c["source_bbox"],
                row=c.get("row_index"),
                column=c.get("column_index"),
                rowspan=c.get("rowspan"),
                colspan=c.get("colspan"),
            )
        st = structs.get(t["table_id"], {})
        nodes = {
            n["node_id"]: n.get("source_cell_id")
            for n in st.get("row_tree", []) + st.get("column_tree", [])
        }
        candidates.extend(
            dict(
                cell_id=m["cell_id"],
                row_path=[nodes.get(n, n) for n in m.get("row_path", [])],
                column_path=[nodes.get(n, n) for n in m.get("column_path", [])],
                state=m.get("evidence_state"),
            )
            for m in st.get("cell_mappings", [])
        )
    contents = {c["content_id"]: c for c in doc.get("contents", [])}
    for t in closure["related_native_tables"]:
        for c in t["cells"]:
            add(
                c["cell_id"],
                "native_cell",
                c.get("text_raw", c.get("text", "")) or "",
                bbox=c.get("source", {}).get("bbox"),
                table_id=t["table_candidate_id"],
            )
    for link in closure["nested_context"]:
        for item in link.get("items", []):
            c = contents.get(item.get("ref_id"))
            if c:
                add(c["content_id"], "content", c.get("text_raw") or "", page=link["page_number"])
    pages = doc.get("document_ir", {}).get("pages", [])
    for p in sorted(pages, key=lambda p: abs(p["page_number"] - page)):
        pn = p["page_number"]
        if abs(pn - page) > 1:
            continue
        spans = [s for b in p.get("blocks", []) for s in b.get("spans", [])]
        for s in spans:
            box = s.get("bbox", (s.get("source") or {}).get("bbox"))
            if pn < page and box and box["y1"] < p["height"] - 280:
                continue
            if pn > page and box and box["y0"] > 280:
                continue
            if len(evidence) >= 300:
                omitted.append(s["span_id"])
                continue
            add(s["span_id"], "span", s["text_raw"], page=pn, bbox=box)
    images = []
    with fitz.open(folder / "input.pdf") as pdf:
        for pn in range(max(1, page - 1), min(len(pdf), page + 1) + 1):
            p = pdf[pn - 1]
            clip = p.rect
            if pn < page:
                clip = fitz.Rect(0, max(0, p.rect.height - 280), p.rect.width, p.rect.height)
            if pn > page:
                clip = fitz.Rect(0, 0, p.rect.width, min(280, p.rect.height))
            image_id = snap["document_id"] + f"/review-page-{pn}"
            add(
                image_id,
                "page_region",
                "",
                page=pn,
                bbox=dict(zip(("x0", "y0", "x1", "y1"), list(clip))),
            )
            images.append(
                dict(
                    source_id=image_id,
                    bytes=p.get_pixmap(matrix=fitz.Matrix(1.7, 1.7), clip=clip).tobytes("png"),
                )
            )
    packet = dict(
        selection=snap["selection"],
        feedback=record["feedback"],
        evidence=evidence,
        extracted_candidates=candidates,
        omitted_ids=omitted,
        extracted_parent_links=closure["nested_context"],
        parent_identity_candidates=closure["nested_parent_identity"],
        continuation_candidates=closure["logical_table_links"],
        scope="selected page plus available parent closure and adjacent page edges; not whole document",
    )
    if len(json.dumps(packet, ensure_ascii=False).encode()) > 160000:
        raise ValueError("EVIDENCE_BUDGET_EXCEEDED")
    return packet, images
