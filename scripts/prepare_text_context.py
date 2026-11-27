"""Offline source-context audit and pilot payload preparation; no API transport."""

# ruff: noqa: E402
import json
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from doc_understanding.agents.semantic_resolver.models import ResolverRequest, SemanticOverlay
from doc_understanding.agents.semantic_resolver.context import (
    build_context,
    ContextResponse,
    TextContextRequest,
)
from doc_understanding.parser.physical.physical_models import PhysicalTableIR
from doc_understanding.parser.structure.models import StructuredTableIR
from doc_understanding.domain.extraction_envelope import StoredArtifact, ExtractionEnvelope


def read(p):
    return json.loads(p.read_text(encoding="utf8"))


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def save(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf8")


def strict_schema(value):
    if isinstance(value, dict):
        result = {k: strict_schema(v) for k, v in value.items() if k != "default"}
        if result.get("type") == "object":
            result["required"] = list(result.get("properties", {}))
            result["additionalProperties"] = False
        return result
    if isinstance(value, list):
        return [strict_schema(v) for v in value]
    return value


def main():
    base, out = ROOT / "data/namwon-holdout", ROOT / "data/text-context-v1"
    pre = read(base / "pilot-plan/preflight.json")
    source_path = Path(pre["baseline_overlay_path"])
    source = SemanticOverlay.model_validate_json(source_path.read_bytes())
    run = read(base / "runs.json")[0]
    physical_path = Path(run["physical_run"])
    physical = [
        PhysicalTableIR.model_validate_json(p.read_bytes())
        for p in (physical_path / "physical").glob("page-*-table-*.json")
    ]
    structured_file = Path(run["semantic_run"]) / "structured-before.json"
    structured = [StructuredTableIR.model_validate(d) for d in read(structured_file)]
    for t in physical:
        assert (
            hashlib.sha256(t.model_dump_json().encode()).hexdigest()
            == source.input_hashes[f"physical:{t.table_id}"]
        )
    for t in structured:
        assert (
            hashlib.sha256(t.model_dump_json().encode()).hexdigest()
            == source.input_hashes[f"structured:{t.table_id}"]
        )
    audits, snapshots = [], {}
    response_schema = strict_schema(ContextResponse.model_json_schema())
    for old in read(base / "live-hardcase/results.json"):
        request_path = base / "pilot-plan" / (old["request_id"] + ".json")
        req = ResolverRequest.model_validate(read(request_path))
        ids = {f.node_id for f in req.nodes}
        before = {
            n.semantic_node_id: n.semantic_parent_id
            for n in source.table_nodes
            if n.semantic_node_id in ids
            and n.relation_type == "parent_child"
            and n.semantic_parent_id is not None
        }
        tids = {n.table_id for n in req.nodes}
        ts = [t for t in physical if t.table_id in tids]
        ss = [s for s in structured if s.table_id in tids]
        reg, context = build_context(req.request_id, req.nodes, ts, ss, before, req.mode)
        # Every outgoing label must be copied from a source feature or structured column header.
        assert [n["label"] for n in context.nodes] == [
            n.text_raw for n in sorted(req.nodes, key=lambda n: (n.order, n.node_id))
        ]
        for n in req.nodes:
            assert n.text_raw == next(f.text_raw for f in source.features if f.node_id == n.node_id)
        body = dict(
            model="gpt-4.1-mini-2025-04-14",
            store=False,
            max_output_tokens=4000,
            input=[
                dict(role="developer", content=context.protocol),
                dict(role="user", content=context.model_dump_json()),
            ],
            text=dict(
                format=dict(
                    type="json_schema", name="context_proposal", strict=True, schema=response_schema
                )
            ),
        )
        bound = len(json.dumps(body, ensure_ascii=False).encode()) + 256
        save(out / (req.request_id + ".request.json"), context.model_dump(mode="json"))
        save(out / (req.request_id + ".binder.local.json"), reg.model_dump(mode="json"))
        save(out / (req.request_id + ".http-body.json"), body)
        audits.append(
            dict(
                request_id=req.request_id,
                mode=req.mode,
                node_count=len(req.nodes),
                full_physical_cells=sum(len(t.cells) for t in ts),
                row_label_cells_omitted=sum(t["omitted_row_label_count"] for t in context.tables),
                matched_node_cells=sum(len(n["physical_cells"]) for n in context.nodes),
                nodes_with_indent=sum(n["indent"] is not None for n in context.nodes),
                nodes_with_boundaries=sum(
                    any(c["boundaries"] for c in n["physical_cells"]) for n in context.nodes
                ),
                merged_label_nodes=sum(
                    any(
                        (c["rowspan"] or 1) > 1 or (c["colspan"] or 1) > 1
                        for c in n["physical_cells"]
                    )
                    for n in context.nodes
                ),
                current_parent_count=len(context.current_parents),
                limitations=context.limitations,
                input_token_conservative_bound=bound,
                max_output_tokens=4000,
                max_cost_at_frozen_rates=bound * 0.4 / 1e6 + 4000 * 1.6 / 1e6,
                context_digest=context.context_digest,
            )
        )
        snapshots[str(request_path)] = sha(request_path)
    for p in [
        source_path,
        structured_file,
        physical_path / "document-ir.json",
        base / "source-annotations.json",
    ]:
        snapshots[str(p)] = sha(p)
    frozen = read(base / "frozen-baseline.json")["files"]
    assert all(sha(ROOT / p) == h for p, h in frozen.items())
    assert sha(source_path) == pre["baseline_overlay_sha256"]
    assert sha(base / "source-annotations.json") == pre["golden_sha256"]
    artifacts = []
    for aid, kind, p in [
        ("document", "DocumentIR", physical_path / "document-ir.json"),
        ("semantic", "SemanticOverlay", source_path),
    ]:
        payload = read(p)
        artifacts.append(
            StoredArtifact(
                artifact_id=aid,
                kind=kind,
                schema_version=payload["schema_version"],
                original_uri=str(p),
                raw_sha256=sha(p),
                payload=payload,
                validation_status="review_required",
                upstream_artifact_ids=("document",) if aid == "semantic" else (),
            )
        )
    envelope = ExtractionEnvelope(
        source_document_id=source.document_id,
        source_version_sha256=pre["source_pdf_sha256"],
        source_uri=read(base / "registry.json")[0]["path"],
        parsing_run_id=str(source.run_id),
        parsing_run_status="partial",
        configuration={
            "example_scope": "document and semantic snapshots only; physical/structured remain referenced by original input_hashes",
            "no_db_write": True,
        },
        artifacts=tuple(artifacts),
    )
    save(out / "canonical-extraction.example.json", envelope.model_dump(mode="json"))
    for name, cls in [
        ("text-structure-context", TextContextRequest),
        ("text-structure-response", ContextResponse),
        ("canonical-extraction-envelope.draft", ExtractionEnvelope),
    ]:
        save(ROOT / "schemas" / (name + ".schema.json"), cls.model_json_schema())
    manifest = dict(
        status="prepared_not_executed",
        dataset_role="development",
        gold_status="provisional",
        new_api_calls=0,
        model="gpt-4.1-mini-2025-04-14",
        max_calls=8,
        retries=0,
        price_basis="frozen prior pilot rates; recheck before actual execution",
        input_per_million=0.4,
        output_per_million=1.6,
        max_cost_at_frozen_rates=sum(a["max_cost_at_frozen_rates"] for a in audits),
        requests=audits,
        source_hashes=snapshots,
        unchanged_frozen_files=len(frozen),
        unchanged_physical_cells=sum(len(t.cells) for t in physical),
        sends="labels, short IDs, native geometry/features, slot topology, boundaries, provisional current parents for Hybrid; no PDF/images/value-cell text/gold",
        live_accuracy="N/A; saved responses cannot test new structural context",
    )
    save(out / "manifest.json", manifest)
    print(
        json.dumps({"requests": audits, "max_cost": manifest["max_cost_at_frozen_rates"]}, indent=2)
    )


if __name__ == "__main__":
    main()
