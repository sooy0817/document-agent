"""Offline replay CLI; never imports a live provider or reads API credentials."""

# ruff: noqa: E402
import argparse
import hashlib
import html
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from doc_understanding.agents.semantic_resolver.models import (
    SemanticOverlay,
    ResolverRequest,
    SemanticPatchProposal,
)
from doc_understanding.agents.semantic_resolver.validator import validate_patches
from doc_understanding.agents.semantic_resolver.repair import (
    Registry,
    Operation,
    Revision,
    CoverageReport,
    AggregationSidecar,
    apply_operations,
    coverage,
)


def read(path):
    return json.loads(path.read_text(encoding="utf8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    base, out = args.evaluation_dir.resolve(), args.output_dir.resolve()
    if out == base or base in out.parents:
        raise ValueError("output must be separate from frozen input")
    out.mkdir(parents=True, exist_ok=True)
    inputs = {str(p): sha(p) for p in base.rglob("*.json")}
    pre = read(base / "pilot-plan/preflight.json")
    source_path = Path(pre["baseline_overlay_path"])
    assert sha(source_path) == pre["baseline_overlay_sha256"]
    assert sha(base / "source-annotations.json") == pre["golden_sha256"]
    source = SemanticOverlay.model_validate_json(source_path.read_bytes())
    stored = read(base / "live-hardcase/results.json")
    gold = read(base / "golden-review.json")
    selected = [c for c in gold["cases"] if c["id"] in pre["evaluated_case_ids"]]
    baseline = {n.semantic_node_id: n for n in source.table_nodes}
    traces, cases, runs, totals = [], [], [], []
    for i, old in enumerate(stored, 1):
        req = ResolverRequest.model_validate(
            read(base / "pilot-plan" / (old["request_id"] + ".json"))
        )
        raw = read(base / f"live-hardcase/response-{i:02d}.json")
        body = "".join(
            c["text"]
            for o in raw["output"]
            for c in o.get("content", [])
            if c["type"] == "output_text"
        )
        proposal = SemanticPatchProposal.model_validate_json(body)
        assert proposal.model_dump(mode="json") == old["proposal"]
        ids = set(req.allowed_node_ids)
        candidates = tuple(c for c in source.candidates if c.parent_id in ids and c.child_id in ids)
        rules = tuple(
            d
            for d in source.decisions
            if d.proposal.parent_id in ids and d.proposal.child_id in ids
        )
        original = validate_patches(
            req.nodes, candidates, proposal.patches, "text_llm", existing=rules
        )
        assert [d.model_dump(mode="json") for d in original] == old["decisions"]
        reg = Registry.create(req.nodes)
        inverse = {v: k for k, v in reg.aliases.items()}
        before = {
            nid: baseline[nid].semantic_parent_id
            for nid in ids
            if baseline[nid].relation_type == "parent_child"
            and baseline[nid].semantic_parent_id in ids
        }
        if req.mode == "llm_only":
            before = {}
        ops, auxiliary = [], []
        local_gold = {
            c["child_anchor"]["node_id"]: c
            for c in selected
            if c["child_anchor"]["node_id"] in ids and c["parent_anchor"]["node_id"] in ids
        }
        for j, decision in enumerate(original):
            p = decision.proposal
            c = local_gold.get(p.child_id)
            known = p.parent_id in ids and p.child_id in ids
            categories = []
            if not known:
                categories.append("id_mapping_error")
            if "MISSING_OR_UNRELATED_EVIDENCE" in decision.reasons:
                categories.append("evidence_validation_error")
            if c and p.relation_type != "parent_child":
                categories.append("relation_type_error")
            if c and known and p.parent_id != c["parent_anchor"]["node_id"]:
                categories.append("semantic_parent_error")
            if any(r in decision.reasons for r in ("SELF_CYCLE", "CYCLE", "REVERSED_ORDER")):
                categories.append("structural_validation_error")
            trace = dict(
                request_id=req.request_id,
                patch_index=j,
                original=p.model_dump(mode="json"),
                original_status=decision.validation_status,
                original_reasons=decision.reasons,
                categories=categories,
                semantic_evaluation="provisional" if c and known else "N/A",
            )
            traces.append(trace)
            if not known:
                trace["repair_status"] = "unbound_rejected_no_fuzzy_repair"
                continue
            if p.relation_type != "parent_child":
                auxiliary.append(p.model_dump(mode="json"))
                trace["repair_status"] = "auxiliary_not_a_hierarchy_parent"
                continue
            op = Operation(
                registry_hash=reg.fingerprint,
                operation="REPLACE" if p.child_id in before else "ADD",
                parent=inverse[p.parent_id],
                child=inverse[p.child_id],
                expected_parent=inverse[before[p.child_id]] if p.child_id in before else None,
                reason=p.reason,
            )
            trace["operation_index"] = len(ops)
            ops.append(op)
        for node_id in proposal.abstain_node_ids:
            if node_id in inverse:
                ops.append(
                    Operation(
                        registry_hash=reg.fingerprint,
                        operation="ABSTAIN",
                        child=inverse[node_id],
                        reason="historical explicit abstention",
                    )
                )
        revision = apply_operations(req.nodes, candidates, reg, ops, before)
        for trace in traces:
            if trace["request_id"] == req.request_id and "operation_index" in trace:
                h = revision.history[trace["operation_index"]]
                trace["repair_status"] = h["status"]
                trace["repair_reasons"] = h["reasons"]
        old_nodes = {n["semantic_node_id"]: n for n in old["nodes"]}
        for constraint in gold["cases"]:
            if constraint["id"] in (
                "negative-total-inventory",
                "negative-loan-total",
                "negative-deposit-total",
            ):
                child = constraint["child_anchor"]["node_id"]
                if child in ids:
                    totals.append(
                        dict(
                            request_id=req.request_id,
                            case_id=constraint["id"],
                            original_parent=old_nodes[child]["semantic_parent_id"],
                            repaired_parent=revision.after.get(child),
                            status="provisional_supplemental_not_in_14_denominator",
                        )
                    )
        for child, c in local_gold.items():
            expected = c["parent_anchor"]["node_id"]
            matching = [p for p in proposal.patches if p.child_id == child]
            raw_correct = any(
                p.parent_id == expected and p.relation_type == "parent_child" for p in matching
            )
            old_correct = (
                old_nodes[child]["semantic_parent_id"] == expected
                and old_nodes[child]["relation_type"] == "parent_child"
            )
            cases.append(
                dict(
                    case_id=c["id"],
                    mode=req.mode,
                    baseline_correct=c["status"] == "correct",
                    semantic_raw_correct=raw_correct,
                    original_correct=old_correct,
                    repair_correct=revision.after.get(child) == expected,
                    expected_parent=expected,
                    repair_parent=revision.after.get(child),
                    no_raw_proposal=not matching,
                    explicit_abstention=child in proposal.abstain_node_ids,
                    original_nonhierarchy_parent=bool(old_nodes[child]["semantic_parent_id"])
                    and old_nodes[child]["relation_type"] != "parent_child",
                )
            )
        run = dict(
            request_id=req.request_id,
            registry=reg.model_dump(mode="json"),
            short_payload=reg.payload(),
            operations=[o.model_dump(mode="json") for o in ops],
            revision=revision.model_dump(mode="json"),
            coverage=CoverageReport(
                node_states=coverage(
                    req.nodes, revision, auxiliary_node_ids={p["child_id"] for p in auxiliary}
                )
            ).model_dump(mode="json"),
            auxiliary_relations=auxiliary,
            aggregation=AggregationSidecar().model_dump(mode="json"),
            aggregation_note="No new semantic aggregation scope inferred during replay",
        )
        save(out / (req.request_id + ".json"), run)
        runs.append(run)
    summary = []
    for mode in ("llm_only", "hybrid"):
        cs = [c for c in cases if c["mode"] == mode]
        ts = [t for t in traces if t["request_id"].endswith(mode)]
        hierarchy = [t for t in ts if t["original"]["relation_type"] == "parent_child"]
        summary.append(
            dict(
                mode=mode,
                evaluated=len(cs),
                raw_semantic_correct=sum(c["semantic_raw_correct"] for c in cs),
                original_correct=sum(c["original_correct"] for c in cs),
                repair_correct=sum(c["repair_correct"] for c in cs),
                baseline_damage_before=sum(
                    c["baseline_correct"] and not c["original_correct"] for c in cs
                ),
                baseline_damage_after=sum(
                    c["baseline_correct"] and not c["repair_correct"] for c in cs
                ),
                newly_damaged=sum(c["original_correct"] and not c["repair_correct"] for c in cs),
                parent_patch_count=len(hierarchy),
                parent_applicable_before=sum(t["original_status"] != "rejected" for t in hierarchy),
                parent_applicable_after=sum(
                    t["repair_status"] == "review_required" for t in hierarchy
                ),
                remaining_wrong=sum(
                    c["repair_parent"] is not None and not c["repair_correct"] for c in cs
                ),
                remaining_missing=sum(c["repair_parent"] is None for c in cs),
            )
        )
    assert all(sha(Path(p)) == h for p, h in inputs.items())
    assert sha(source_path) == pre["baseline_overlay_sha256"]
    frozen = read(base / "frozen-baseline.json")["files"]
    assert all(sha(ROOT / p) == h for p, h in frozen.items())
    report = dict(
        schema_version="semantic-replay-report/1.0",
        dataset_role="development",
        gold_status="provisional",
        new_api_calls=0,
        new_api_cost_usd=0,
        replayed_responses=len(stored),
        source_hashes=inputs,
        frozen_files_unchanged=len(frozen),
        counterfactual="exact endpoint aliases + deterministic evidence; no new model judgments",
        summary=summary,
        trace_counts=dict(Counter(c for t in traces for c in t["categories"])),
        nonhierarchy_parent_composition_errors=sum(
            c["original_nonhierarchy_parent"] for c in cases
        ),
        cases=cases,
        traces=traces,
        supplemental_totals=totals,
        coverage_counts=dict(
            Counter(state for run in runs for state in run["coverage"]["node_states"].values())
        ),
        holdout="Nampyeong not opened or evaluated",
    )
    save(out / "report.json", report)
    save(
        out / "dataset-partitions.json",
        {
            "namwon_14": {
                "role": "development",
                "gold_status": "provisional",
                "used_for_repair": True,
            },
            "nampyeong": {
                "role": "unused_holdout",
                "opened": False,
                "evaluated": False,
                "note": "No file discovery or extraction performed in this phase",
            },
        },
    )
    for name, cls in (
        ("semantic-operation", Operation),
        ("semantic-id-registry", Registry),
        ("semantic-revision", Revision),
        ("semantic-coverage", CoverageReport),
        ("aggregation-sidecar", AggregationSidecar),
    ):
        save(ROOT / "schemas" / (name + ".schema.json"), cls.model_json_schema())
    (out / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>C1.1 offline replay</title>'
        "<h1>C1.1 재생 / provisional 개발셋</h1><p>새 API 호출 0 · 의미 판단 개선 실험 아님</p>"
        '<a href="report.json">전체 Trace JSON</a><pre>'
        + html.escape(
            json.dumps(
                {"summary": summary, "counts": report["trace_counts"], "cases": cases},
                ensure_ascii=False,
                indent=2,
            )
        )
        + "</pre>",
        encoding="utf8",
    )
    print(
        json.dumps(
            {
                "summary": summary,
                "counts": report["trace_counts"],
                "composition_errors": report["nonhierarchy_parent_composition_errors"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
