"""Whole-document physical evaluation, selected pages control order, never correctness."""

import hashlib
import json
from pathlib import Path

import pymupdf

from doc_understanding.domain.contracts import ExtractionRequest
from doc_understanding.extraction.physical_evidence_adapter import raster_evidence
from doc_understanding.extraction.pymupdf_adapter import PyMuPDFExtractor
from doc_understanding.ingestion.artifacts import LocalArtifactStore
from doc_understanding.orchestrator.workflow import extract_ir
from doc_understanding.physical_experiment.fill_boundaries import FillBoundaryDetector
from doc_understanding.table_understanding.line_detector import LineDetector
from doc_understanding.table_understanding.physical_grid import PhysicalGridReconstructor
from doc_understanding.table_understanding.physical_models import GridPolicy
from doc_understanding.table_understanding.nested_regions import split_nested_regions


def debug_overlay(pdf_bytes, page_index, result, dpi=120):
    """Render the original page plus exact-coordinate debug graphics, not generated imagery."""
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        page = doc[page_index]
        page.set_rotation(0)
        shape = page.new_shape()
        for ti, table in enumerate(result.tables):
            shape.draw_rect(pymupdf.Rect(table.bounding_box.xyxy))
            shape.finish(color=(0.1, 0.2, 0.9), width=1.6)
            for cell in table.cells:
                color = (
                    (0.95, 0.4, 0)
                    if cell.status != "resolved"
                    else (0, 0.6, 0.2)
                    if cell.rowspan > 1 or cell.colspan > 1
                    else (0.1, 0.45, 0.9)
                )
                rect = pymupdf.Rect(cell.source_bbox.xyxy)
                shape.draw_rect(rect)
                shape.finish(color=color, width=0.7)
                label = f"r{cell.row_index}c{cell.column_index}:{cell.rowspan or '?'}x{cell.colspan or '?'}"
                shape.insert_text(
                    (rect.x0 + 1, rect.y0 + 5), label, fontsize=4.5, color=color
                )
            for edge in table.unresolved_boundaries:
                points = (
                    ((edge.start, edge.position), (edge.end, edge.position))
                    if edge.axis == "horizontal"
                    else ((edge.position, edge.start), (edge.position, edge.end))
                )
                shape.draw_line(*points)
                shape.finish(color=(1, 0, 0), width=1.3, dashes="[3 2]")
        shape.commit(overlay=True)
        return page.get_pixmap(dpi=dpi, alpha=False).tobytes("png")


def evaluate_pdf(
    pdf_path: Path,
    output_root: Path,
    focus_pages=(8, 14, 15, 18),
    all_pages=True,
    mode="auto",
    label="unreviewed_real_pdf",
    *,
    boundary_profile="stroke_fill",
    nested_regions=True,
):
    # Promote the frozen F0 adapter at the orchestration boundary, not in the
    # low-level reconstructor used by independent S0/S1 experiments.
    if boundary_profile not in ("stroke", "stroke_fill"):
        raise ValueError("unknown boundary profile")
    policy = GridPolicy()
    detector = FillBoundaryDetector(policy) if boundary_profile == "stroke_fill" else LineDetector(policy)
    reconstructor = PhysicalGridReconstructor(policy, line_detector=detector)
    data = pdf_path.read_bytes()
    ir = extract_ir(ExtractionRequest(data, pdf_path.name), PyMuPDFExtractor(), output_root)
    run = output_root / str(ir.run.run_id)
    store = LocalArtifactStore(run)
    selected = [p for p in focus_pages if 1 <= p <= len(ir.pages)]
    if all_pages:
        selected += [p for p in range(1, len(ir.pages) + 1) if p not in selected]
    results = []
    table_rows = []
    nested_logs = []
    for number in selected:
        page = ir.pages[number - 1]
        needs_pixels = mode in ("raster", "mixed") or not any(
            b.kind == "drawing" for b in page.blocks
        )
        pixels = raster_evidence(page, run) if needs_pixels else None
        result = reconstructor.analyze_page(ir.document_id, page, pixels, mode)
        prefix = f"physical/page-{number:03d}"
        if nested_regions:
            flat = result
            result, nested_log = split_nested_regions(ir.document_id, page, flat)
            nested_logs.append(nested_log)
            store.write(f"{prefix}-nested.json", json.dumps(nested_log,ensure_ascii=False,indent=2).encode('utf8'), "application/json")
            if nested_log['candidates']:
                store.write(f"{prefix}-flat.json", flat.model_dump_json(indent=2).encode('utf8'), "application/json")
        store.write(
            f"{prefix}.json", result.model_dump_json(indent=2).encode("utf-8"), "application/json"
        )
        image = store.write(
            f"{prefix}-overlay.png", debug_overlay(data, number - 1, result), "image/png"
        )
        store.write(
            f"{prefix}-lines.json",
            json.dumps(
                [
                    {
                        "table_id": t.table_id,
                        "lines": [evidence.model_dump(mode="json") for evidence in t.lines],
                    }
                    for t in result.tables
                ],
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8"),
            "application/json",
        )
        for index, table in enumerate(result.tables):
            path = f"physical/page-{number:03d}-table-{index:03d}.json"
            store.write(path, table.model_dump_json(indent=2).encode("utf-8"), "application/json")
            table_rows.append(
                {
                    "page": number,
                    "table_index": index,
                    "bbox": table.bounding_box.xyxy,
                    "state": table.analysis_state,
                    "json": path,
                    "overlay": image.path,
                    "cells": len(table.cells),
                    "rowspans": sum((c.rowspan or 0) > 1 for c in table.cells),
                    "colspans": sum((c.colspan or 0) > 1 for c in table.cells),
                    "unresolved_boundaries": len(table.unresolved_boundaries),
                }
            )
        results.append(result)
    failures = sum(i.code == "REGION_ANALYSIS_FAILED" for r in results for i in r.diagnostics)
    states = [t.analysis_state for r in results for t in r.tables]
    summary = {
        "dataset_label": label,
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "document_pages": len(ir.pages),
        "evaluated_pages": selected,
        "mode": mode,
        "boundary_profile": boundary_profile,
        "boundary_detector_version": "fill-centerline-f0/1" if boundary_profile == "stroke_fill" else "stroke/1",
        "nested_region_profile": "native-scoped/1" if nested_regions else "disabled",
        "nested_candidates": sum(len(r['candidates']) for r in nested_logs),
        "nested_splits": sum(r['applied'] for r in nested_logs),
        "detected_table_regions": len(states) + failures,
        "automatic_structure_pass": states.count("validated"),
        "partial_reconstruction": states.count("partial"),
        "failed_or_review_required": sum(s in ("invalid", "review_required") for s in states)
        + failures,
        "pages_without_detected_grid": [r.page_number for r in results if not r.tables],
        "page_analysis_failures": [
            r.page_number for r in results
            if any(i.severity == "error" and i.code != "REGION_ANALYSIS_FAILED" for i in r.diagnostics)
        ],
        "unassigned_original_cells": sum(len(r.unassigned_source_cells) for r in results),
        "accuracy_proven": False,
        "manual_golden_comparison": "not_performed",
        "detection_recall": "unknown; no manually annotated whole-document denominator",
        "success_criterion": "All observed elementary slots accounted for; closed rectangular cells; supported boundaries; consistent source links. Geometry checks only.",
        "tables": table_rows,
    }
    store.write(
        "physical/summary.json",
        json.dumps(summary, ensure_ascii=False, indent=2).encode(),
        "application/json",
    )
    return run, summary
