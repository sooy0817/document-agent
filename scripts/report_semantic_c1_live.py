"""Offline scoring of frozen live results against pre-existing selected expectations."""

# ruff: noqa: E402
import hashlib
import json
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
from tests.test_semantic_resolver_real import GOLD, label
from doc_understanding.agents.semantic_resolver.models import SemanticOverlay, ResolverRequest
from doc_understanding.agents.semantic_resolver.engine import assemble_nodes, digest
from doc_understanding.agents.semantic_resolver.candidates import build_candidates
from doc_understanding.agents.semantic_resolver.validator import validate_patches
from doc_understanding.agents.semantic_resolver.models import SemanticPatch
from doc_understanding.domain.models import DocumentIR
from doc_understanding.parser.physical.physical_models import PhysicalTableIR
from doc_understanding.parser.structure.models import StructuredTableIR


def main():
    folder = ROOT / "data/semantic-c1-live-pilot"

    def read(p):
        return json.loads(p.read_text(encoding="utf8"))

    manifest, preflight, results = (
        read(folder / f"{n}.json") for n in ("manifest", "preflight", "results")
    )
    corpus = {x["slug"]: x for x in read(ROOT / "data/semantic-c1/manifest.json")}
    checks, rows, details, calls = [], [], [], []
    for item, frozen, result in zip(
        manifest["requests"], preflight["requests"], results, strict=True
    ):
        req = ResolverRequest.model_validate_json((folder / item["file"]).read_bytes())
        slug = req.request_id.rsplit("-", 1)[0]
        source_path = Path(item["source_overlay"])
        assert (
            hashlib.sha256(source_path.read_bytes()).hexdigest() == frozen["source_overlay_sha256"]
        )
        source = SemanticOverlay.model_validate_json(source_path.read_bytes())
        features = {n.node_id: n for n in req.nodes}
        names = {i: label(n.text_raw) for i, n in features.items()}
        expected_paths = [
            c["labels"]
            for c in GOLD["paths"]
            if c["document"] == slug and all(n in names.values() for n in c["labels"])
        ]
        expected = {(a, b) for p in expected_paths for a, b in zip(p, p[1:])}
        rules = tuple(
            d
            for d in source.decisions
            if d.proposal.parent_id in names and d.proposal.child_id in names
        )
        local_candidates = build_candidates(req.nodes, ())
        local_proposals = tuple(
            SemanticPatch(
                parent_id=c.parent_id,
                child_id=c.child_id,
                relation_type=c.relation_type,
                evidence_ids=list(c.evidence_ids),
                reason="local rule baseline",
            )
            for c in local_candidates
        )
        local_rules = validate_patches(req.nodes, local_candidates, local_proposals)

        def edge_keys(ds):
            return {
                (d.proposal.parent_id, d.proposal.child_id, d.proposal.relation_type)
                for d in ds
                if d.validation_status != "rejected"
            }

        assert edge_keys(local_rules) == edge_keys(rules)
        rules = local_rules
        baseline_nodes = [
            n.model_dump(mode="json") for n in assemble_nodes(req.nodes, rules, uuid4(), None)
        ]
        variants = [(req.mode, result["nodes"])]
        if req.mode == "llm_only":
            variants.insert(0, ("rule_only", baseline_nodes))
        for mode, nodes in variants:
            edges = {
                (names[n["semantic_parent_id"]], names[n["semantic_node_id"]])
                for n in nodes
                if n["semantic_parent_id"] and n["relation_type"] == "parent_child"
            }
            paths = [[names[i] for i in n["hierarchy_path"]] for n in nodes]
            rows.append(
                dict(
                    document=slug,
                    mode=mode,
                    correct=len(edges & expected),
                    expected=len(expected),
                    wrong=sorted(edges - expected),
                    missing=sorted(expected - edges),
                    precision=len(edges & expected) / len(edges) if edges else None,
                    recall=len(edges & expected) / len(expected),
                    exact_paths=sum(p in paths for p in expected_paths),
                    path_total=len(expected_paths),
                    paths=paths,
                )
            )
        proposals = result["proposal"]["patches"]
        for p in proposals:
            assert p["parent_id"] in names and p["child_id"] in names
            assert set(p["evidence_ids"]) <= set(req.allowed_evidence_ids)
        rule_edges = {
            (d.proposal.parent_id, d.proposal.child_id, d.proposal.relation_type)
            for d in rules
            if d.validation_status != "rejected"
        }
        details.append(
            dict(
                request_id=req.request_id,
                proposals=[
                    dict(
                        parent=names[p["parent_id"]],
                        child=names[p["child_id"]],
                        relation=p["relation_type"],
                        reason=p["reason"],
                    )
                    for p in proposals
                ],
                abstain_labels=[names.get(i, i) for i in result["proposal"]["abstain_node_ids"]],
                rule_disagreements=[
                    p
                    for p in proposals
                    if (p["parent_id"], p["child_id"], p["relation_type"]) not in rule_edges
                ],
                rejected=[d for d in result["decisions"] if d["validation_status"] == "rejected"],
            )
        )
        assert all(d["validation_status"] == "review_required" for d in result["decisions"])
        calls.append(result["call"])
        if req.mode == "llm_only":
            entry = corpus[slug]
            assert hashlib.sha256(Path(entry["path"]).read_bytes()).hexdigest() == entry["sha256"]
            run = Path(entry["run"])
            doc = DocumentIR.model_validate_json((run / "document-ir.json").read_bytes())
            assert digest(doc) == req.input_hashes["document"]
            count = 0
            for p in (run / "physical").glob("page-*-table-*.json"):
                table = PhysicalTableIR.model_validate_json(p.read_bytes())
                assert digest(table) == req.input_hashes[f"physical:{table.table_id}"]
                count += len(table.cells)
            structured = read(source_path.parent / "structured-before.json")
            for t in structured:
                table = StructuredTableIR.model_validate(t)
                assert digest(table) == req.input_hashes[f"structured:{table.table_id}"]
            checks.append(
                dict(
                    document=slug,
                    source_pdf_unchanged=True,
                    document_ir_unchanged=True,
                    physical_ir_unchanged=True,
                    structured_ir_unchanged=True,
                    overlay_unchanged=True,
                    physical_cells_checked=count,
                )
            )
    for path, sha in read(ROOT / "data/semantic-c1/frozen-contracts.json").items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == sha
    report = dict(
        evaluation_status=GOLD["status"],
        scope="selected two first-page sections only; not whole-document accuracy",
        actual_calls=sum(c["attempts"] for c in calls),
        mock_calls=0,
        model_id=manifest["model_id"],
        input_tokens=sum(c["input_tokens"] for c in calls),
        output_tokens=sum(c["output_tokens"] for c in calls),
        estimated_cost_usd=sum(c["estimated_cost_usd"] for c in calls),
        total_call_seconds=sum(c["elapsed_seconds"] for c in calls),
        calls=calls,
        comparison=rows,
        proposal_details=details,
        immutability=checks,
    )
    assert report["actual_calls"] <= 4 and report["estimated_cost_usd"] < 0.05
    (folder / "comparison.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8"
    )
    print(
        json.dumps(
            {
                k: v
                for k, v in report.items()
                if k not in ("calls", "proposal_details", "comparison")
            },
            ensure_ascii=True,
        )
    )
    print(json.dumps(rows, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
