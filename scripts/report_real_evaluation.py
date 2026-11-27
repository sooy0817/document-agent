"""Local evidence reports and pixel-only ablation; no semantic interpretation."""

import argparse
import hashlib
import html
import json
from pathlib import Path

from doc_understanding.domain.models import DocumentIR
from doc_understanding.parser.physical.runner import debug_overlay
from doc_understanding.parser.extraction.physical_evidence_adapter import raster_evidence
from doc_understanding.parser.physical.physical_grid import PhysicalGridReconstructor
from doc_understanding.parser.physical.physical_models import GridPolicy


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def report(run, folder):
    """Expandable per-table evidence, including every cell and every line."""
    sections = []
    inventory = []
    for path in sorted(folder.glob("page-???.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        number = result["page_number"]
        sections.append(
            f'<h2>PDF page {number}</h2><a href="page-{number:03d}-overlay.png">Overlay PNG</a>'
        )
        sections.append(
            f"<p>Page diagnostics: {html.escape(json.dumps(result['diagnostics']))}; "
            f"unassigned source cells: {len(result['unassigned_source_cells'])}</p>"
        )
        for index, table in enumerate(result["tables"]):
            name = f"page-{number:03d}-table-{index:03d}.json"
            write_json(folder / name, table)
            counts = {
                axis: sum(x["axis"] == axis for x in table["lines"])
                for axis in ("horizontal", "vertical")
            }
            row = dict(
                page=number,
                table=index,
                bbox=table["bounding_box"],
                state=table["analysis_state"],
                line_counts=counts,
                cells=len(table["cells"]),
                resolved_cells=sum(c["status"] == "resolved" for c in table["cells"]),
                inferred_cells=sum(c["status"] == "inferred" for c in table["cells"]),
                unresolved_cells=sum(c["status"] == "unresolved" for c in table["cells"]),
                unresolved_boundaries=len(table["unresolved_boundaries"]),
                issues=table["validation_issues"],
                json=name,
            )
            inventory.append(row)
            sections.append(
                f"<h3>Table {index} — {table['analysis_state']}</h3>"
                f"<p>{html.escape(json.dumps(row, ensure_ascii=False))}</p>"
                f'<a href="{name}">PhysicalTableIR JSON</a>'
            )
            for label, key in [
                ("Cells: spans, occupied slots, bbox", "cells"),
                ("Horizontal / vertical lines", "lines"),
                ("Unresolved boundaries", "unresolved_boundaries"),
                ("Validation issues", "validation_issues"),
            ]:
                sections.append(
                    f"<details><summary>{label}</summary><pre>"
                    + html.escape(json.dumps(table[key], ensure_ascii=False, indent=2))
                    + "</pre></details>"
                )
    write_json(folder / "inventory.json", inventory)
    (folder / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Physical grid evidence</title>'
        "<style>body{font:16px sans-serif;max-width:1100px;margin:40px auto;padding:20px}"
        "pre{white-space:pre-wrap;background:#f5f5f5;padding:16px}p{overflow-wrap:anywhere}"
        "details{margin:12px 0}h2{border-top:2px solid #888;padding-top:25px}</style>"
        "<h1>Physical grid evidence</h1><p>PDF page numbers are 1-based. Rows/columns/slots "
        "are 0-based. BBox: unrotated CropBox, top-left, PDF points. Blue: cells; "
        "green: resolved merge; orange: inferred/unresolved cell; red dashed: boundary requiring review. "
        "Automatic geometry pass does not establish accuracy. No semantic or financial validation.</p>"
        + "".join(sections),
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--pixel-only-pages", default="")
    parser.add_argument("--pixel-output", default="pixel-only")
    parser.add_argument(
        "--raster-strategy",
        choices=["legacy_dark", "adaptive_dark", "adaptive_contrast"],
        default="adaptive_contrast",
    )
    args = parser.parse_args()
    run = args.run
    report(run, run / "physical")
    if not args.pixel_only_pages:
        return
    folder = run / args.pixel_output
    folder.mkdir(exist_ok=False)
    ir = DocumentIR.model_validate_json((run / "document-ir.json").read_bytes())
    pdf = (run / "source.pdf").read_bytes()
    results = []
    for number in map(int, args.pixel_only_pages.split(",")):
        original = ir.pages[number - 1]
        # Native blocks, table hints and cell text MUST NOT leak into the ablation.
        page = original.model_copy(
            update={
                "blocks": (),
                "tables": (),
                "text_status": "ocr_required",
                "evidence_sha256": hashlib.sha256(
                    (run / original.image.artifact.path).read_bytes()
                ).hexdigest(),
            }
        )
        result = PhysicalGridReconstructor(
            GridPolicy(raster_strategy=args.raster_strategy)
        ).analyze_page(ir.document_id, page, raster_evidence(original, run), mode="raster")
        write_json(folder / f"page-{number:03d}.json", result.model_dump(mode="json"))
        (folder / f"page-{number:03d}-overlay.png").write_bytes(
            debug_overlay(pdf, number - 1, result)
        )
        states = [t.analysis_state for t in result.tables]
        results.append(
            dict(
                page=number,
                detected=len(states),
                automatic_pass=states.count("validated"),
                partial=states.count("partial"),
                failed=sum(s in ("invalid", "review_required") for s in states),
                diagnostics=[i.model_dump(mode="json") for i in result.diagnostics],
            )
        )
        print(f"Pixel-only page {number}: {len(states)} regions", flush=True)
    write_json(
        folder / "summary.json",
        {
            "dataset_label": "real_pdf_pixel_only_ablation_not_independent_scan",
            "source_sha256": ir.source_sha256,
            "native_evidence_used": False,
            "raster_strategy": args.raster_strategy,
            "ocr_performed": False,
            "accuracy_proven": False,
            "render_dpi": ir.pages[0].image.dpi,
            "pages": results,
        },
    )
    report(run, folder)


if __name__ == "__main__":
    main()
