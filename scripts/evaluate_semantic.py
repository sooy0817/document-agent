"""Reproduce separated real-source review and non-financial synthetic evaluations."""

# ruff: noqa: E402
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
from doc_understanding.parser.physical.runner import evaluate_pdf
from doc_understanding.services.structure_review import write_structure_review
from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer, path_for
from doc_understanding.parser.structure.models import StructuredTableIR, VisionStructureRequest, VisionStructureProposal
from tests.semantic_fixtures import compound, fixture_pdf
from tests.test_semantic_real import CASES, DATA, load, norm


def main():
    source = DATA.parent / "source.pdf"
    real_run, real_summary = write_structure_review(
        sorted(DATA.glob("page-*-table-*.json")),
        ROOT / "data/semantic-real",
        source,
        (8, 14, 15, 18),
    )
    checks = []
    for category in ("row_paths", "forbidden_row_paths", "column_children"):
        for case in CASES[category]:
            result = SemanticTableAnalyzer().analyze(load(case["page"], case["table"]))
            if category == "column_children":
                parents = [n for n in result.column_tree if norm(n.label_raw) == case["parent"]]
                passed = (
                    len(parents) == 1
                    and [
                        norm(n.label_raw)
                        for n in result.column_tree
                        if n.parent_id == parents[0].node_id
                    ]
                    == case["children"]
                )
            else:
                idx = {n.node_id: n for n in result.row_tree}
                paths = [
                    [norm(idx[i].label_raw) for i in path_for(n, result.row_tree)]
                    for n in result.row_tree
                ]
                passed = (case["labels"] in paths) == (category == "row_paths")
            checks.append({"category": category, "case": case, "passed": passed})
    generic = []
    for kind in ("laboratory", "warehouse", "survey"):
        folder = ROOT / "data/semantic-generic" / kind
        folder.mkdir(parents=True, exist_ok=True)
        pdf = folder / f"{kind}.pdf"
        pdf.write_bytes(fixture_pdf(kind))
        table, rows, columns = compound(kind)
        (folder / "authored-physical-input.json").write_text(
            table.model_dump_json(indent=2), encoding="utf8"
        )
        (folder / "expected-edges.json").write_text(
            json.dumps(
                {
                    "origin": "synthetic author specification; no prediction copied",
                    "row_edges": sorted(rows),
                    "column_edges": sorted(columns),
                },
                indent=2,
            ),
            encoding="utf8",
        )
        physical_run, _ = evaluate_pdf(pdf, folder / "extracted", (), True)
        run, summary = write_structure_review(
            sorted((physical_run / "physical").glob("page-*-table-*.json")), folder / "results", pdf
        )
        generic.append({"dataset": kind, "result": str(run), "summary": summary})
    for name, model in (
        ("structured-table-ir", StructuredTableIR),
        ("vision-structure-request", VisionStructureRequest),
        ("vision-structure-proposal", VisionStructureProposal),
    ):
        (ROOT / "schemas" / f"{name}.schema.json").write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2), encoding="utf8"
        )
    report = {
        "real": {
            "run": str(real_run),
            "summary": real_summary,
            "selected_assertions": checks,
            "review_status": CASES["review_status"],
            "full_row_edge_precision": None,
            "full_row_edge_recall": None,
            "full_column_edge_precision": None,
            "full_column_edge_recall": None,
            "full_cell_path_accuracy": None,
            "pending": CASES["pending"],
        },
        "generic": generic,
        "llm_calls": 0,
    }
    (ROOT / "reports/phase2b-evaluation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8"
    )
    print(real_run / "index.html")
    print(f"Real selected assertions: {sum(c['passed'] for c in checks)}/{len(checks)}")


if __name__ == "__main__":
    main()
