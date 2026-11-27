"""New offline run only; preserves baseline artifacts and reference annotations."""

import json
from collections import Counter
from pathlib import Path

from doc_understanding.domain.models import DocumentIR
from doc_understanding.evaluation.parser.tree_accuracy import digest, evaluate_table, micro, read
from doc_understanding.agents.semantic_resolver.group_context import propose_intervals
from doc_understanding.agents.semantic_resolver.models import SemanticFeature, RelationCandidate
from doc_understanding.agents.semantic_resolver.repair import (
    Registry,
    Operation,
    apply_operations,
    coverage,
)
from doc_understanding.parser.physical.physical_grid import PhysicalGridReconstructor
from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/tree-phase3a-repair-v3"


def save(name, value):
    p = OUT / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    if (OUT / "results.json").exists():
        raise SystemExit(
            "Existing repair run preserved; choose a new output directory for another run."
        )
    run = read(ROOT / "data/namwon-holdout/runs.json")[0]
    pr, sr = Path(run["physical_run"]), Path(run["semantic_run"])
    gp = ROOT / "data/tree-phase3a/golden.provisional.json"
    golden = read(gp)
    overlay = read(sr / "semantic-overlay.json")
    snapshots = {
        str(gp): digest(gp),
        str(pr / "document-ir.json"): digest(pr / "document-ir.json"),
        str(sr / "semantic-overlay.json"): digest(sr / "semantic-overlay.json"),
    }
    doc = DocumentIR.model_validate(read(pr / "document-ir.json"))
    features = [SemanticFeature.model_validate(f) for f in overlay["features"]]
    bycell = {f.source_cell_ids[0]: f for f in features if len(f.source_cell_ids) == 1}
    nodecell = {f.node_id: f.source_cell_ids[0] for f in features if len(f.source_cell_ids) == 1}
    all_before = {
        n["semantic_node_id"]: n["semantic_parent_id"]
        for n in overlay["table_nodes"]
        if n["semantic_parent_id"]
    }
    oldst = {s["table_id"]: s for s in read(sr / "structured-before.json")}
    before_results = []
    after_results = []
    text_changes = []
    traces = []
    geometric_changes = []
    for page in doc.pages:
        refs = [g for g in golden["tables"] if g["page"] == page.page_number]
        if not refs:
            continue
        rebuilt = PhysicalGridReconstructor().analyze_page(doc.document_id, page)
        for g in refs:
            oldp = pr / f"physical/page-{page.page_number:03}-table-{g['index']:03}.json"
            snapshots[str(oldp)] = digest(oldp)
            original = read(oldp)
            table = next(t for t in rebuilt.tables if t.table_id == original["table_id"])
            tabledata = table.model_dump(mode="json")
            structured = SemanticTableAnalyzer().analyze(table).model_dump(mode="json")
            # Generator sees physical/structured outputs only, never the reference labels/edges.
            proposals = propose_intervals(tabledata, structured)
            fs = [f for f in features if f.table_id == table.table_id]
            registry = Registry.create(fs)
            aliases = {n: a for a, n in registry.aliases.items()}
            existing = {c: p for c, p in all_before.items() if c in aliases and p in aliases}
            operations = []
            candidates = []
            unbound = []
            for p in proposals["proposals"]:
                child = bycell.get(p["child_cell_id"])
                parent = bycell.get(p["parent_cell_id"])
                if (
                    not child
                    or not parent
                    or child.node_id not in aliases
                    or parent.node_id not in aliases
                ):
                    unbound.append(p)
                    continue
                if existing.get(child.node_id) == parent.node_id:
                    continue
                old = existing.get(child.node_id)
                candidates.append(
                    RelationCandidate(
                        candidate_id=f"interval/{parent.node_id}/{child.node_id}",
                        parent_id=parent.node_id,
                        child_id=child.node_id,
                        relation_type="parent_child",
                        evidence_ids=tuple(p["evidence_cell_ids"]),
                        methods=("sparse_heading_interval",),
                    )
                )
                operations.append(
                    Operation(
                        registry_hash=registry.fingerprint,
                        operation="REPLACE" if old else "ADD",
                        child=aliases[child.node_id],
                        parent=aliases[parent.node_id],
                        expected_parent=aliases[old] if old else None,
                        reason=p["reason"],
                    )
                )
            revision = apply_operations(fs, candidates, registry, operations, existing)
            bparents = {
                nodecell[c]: nodecell.get(p, p) for c, p in all_before.items() if c in nodecell
            }
            aparents = dict(bparents)
            aparents.update(
                {
                    nodecell[c]: nodecell.get(p, p)
                    for c, p in revision.after.items()
                    if c in nodecell
                }
            )
            before_results.append(evaluate_table(g, original, oldst[table.table_id], bparents))
            after_results.append(evaluate_table(g, tabledata, structured, aparents))
            oldcells = {c["cell_id"]: c for c in original["cells"]}
            for c in tabledata["cells"]:
                old = oldcells.get(c["cell_id"])
                if old and old["original_text"] != c["original_text"]:
                    text_changes.append(
                        {
                            "cell_id": c["cell_id"],
                            "before": old["original_text"],
                            "after": c["original_text"],
                            "span_refs": c["span_refs"],
                        }
                    )
                if (
                    not old
                    or old["source_bbox"] != c["source_bbox"]
                    or old["occupied_slots"] != c["occupied_slots"]
                ):
                    geometric_changes.append(c["cell_id"])
            trace = {
                "table_id": table.table_id,
                "proposals": proposals,
                "unbound_proposals": unbound,
                "revision": revision.model_dump(mode="json"),
                "coverage": coverage(fs, revision),
            }
            traces.append(trace)
            save(f"tables/p{g['page']}-t{g['index']}-physical.json", tabledata)
            save(f"tables/p{g['page']}-t{g['index']}-structured.json", structured)
    comparison = []
    for b, a in zip(before_results, after_results):
        for code in ["ROW_PATH", "CELL_PATH", "EVIDENCE"]:
            old = {i["target"] for i in b["issues"] if i["code"] == code}
            new = {i["target"] for i in a["issues"] if i["code"] == code}
            comparison.append(
                {
                    "table": a["table_id"],
                    "code": code,
                    "recovered": sorted(old - new),
                    "regressed": sorted(new - old),
                    "remaining": len(new),
                }
            )
    report = {
        "dataset_role": "Namwon development only",
        "mode": "deterministic offline Rule repair; NOT a new Hybrid or LLM result",
        "reference_review": "user confirmed displayed trees; exhaustive bbox/cell audit still provisional",
        "before": {"micro": micro(before_results), "tables": before_results},
        "after": {"micro": micro(after_results), "tables": after_results},
        "changes": comparison,
        "text_changes": text_changes,
        "geometric_changes": geometric_changes,
        "operation_statuses": dict(
            Counter(h["status"] for t in traces for h in t["revision"]["history"])
        ),
        "external_api_calls": 0,
        "original_inputs_unchanged": all(digest(p) == h for p, h in snapshots.items()),
        "input_hashes": snapshots,
        "limitations": [
            "No automatic root deletion or aggregate scope inference",
            "Merged sparse headings are hypotheses, all review_required",
            "p46 boundary ambiguity not forced into a merged cell",
            "No new document or Nampyeong evaluation",
        ],
    }
    save("traces.json", traces)
    save("results.json", report)
    save(
        "implementation-freeze.json",
        {
            "files": {
                str(p.relative_to(ROOT)): digest(p)
                for p in (ROOT / "src/doc_understanding").rglob("*.py")
            },
            "settings": {"label_blind": True, "single_panel_only": True, "new_calls": 0},
        },
    )
    print(
        json.dumps(
            {
                k: report[k]
                for k in [
                    "mode",
                    "text_changes",
                    "geometric_changes",
                    "operation_statuses",
                    "original_inputs_unchanged",
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(
        json.dumps(
            {"before": report["before"]["micro"], "after": report["after"]["micro"]},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
