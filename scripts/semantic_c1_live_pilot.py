"""Explicitly scoped optional live experiment. Default only prepares reviewable text.

Evaluation selectors below choose user-requested cases, NEVER production hierarchy rules.
Execution reads the frozen packet hashes; it does not reread or upload PDFs.
"""

# ruff: noqa: E402
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
from doc_understanding.agents.semantic_resolver.models import (
    ResolverConfig,
    ResolverRequest,
    SemanticOverlay,
)
from doc_understanding.agents.semantic_resolver.providers import (
    OpenAITextAdapter,
    token_upper_bound,
    payload_for,
)
from doc_understanding.agents.semantic_resolver.validator import validate_patches
from doc_understanding.agents.semantic_resolver.engine import assemble_nodes
from doc_understanding.agents.semantic_resolver.candidates import evidence_ids
from uuid import uuid4

FOLDER = ROOT / "data/semantic-c1-live-pilot"


def prepare():
    FOLDER.mkdir(parents=True, exist_ok=True)
    config = ResolverConfig(
        model_id="gpt-4.1-mini-2025-04-14",
        timeout_seconds=30,
        retries=0,
        concurrency=1,
        max_calls=4,
        max_input_tokens=25000,
        max_output_tokens=1500,
        total_token_budget=106000,
        max_nodes_per_request=10,
        allow_external=False,
        input_price_per_million=0.4,
        output_price_per_million=1.6,
    )
    (FOLDER / "config.json").write_text(config.model_dump_json(indent=2), encoding="utf8")
    runs = json.loads((ROOT / "reports/phase2c1-runs.json").read_text(encoding="utf8"))
    entries = []
    for slug, wanted in [
        (
            "income-integrated",
            (
                "영업수익",
                "신용사업영업수익",
                "이자수익",
                "예치금이자",
                "경제사업영업수익",
                "상품매출액",
            ),
        ),
        ("capital", ("증자", "출자금납입", "회전출자금납입", "가입금납입", "우선출자납입")),
    ]:
        entry = next(r for r in runs if r["slug"] == slug)
        overlay_path = Path(entry["runs"]["rule_only"]["path"]) / "semantic-overlay.json"
        overlay = SemanticOverlay.model_validate_json(overlay_path.read_bytes())
        from tests.test_semantic_resolver_real import label

        nodes = tuple(
            f.model_copy(update={"value_cell_ids": ()})
            for f in overlay.features
            if f.axis == "row" and f.page_number == 1 and label(f.text_raw) in wanted
        )
        ids = {f.node_id for f in nodes}
        candidates = tuple(
            c for c in overlay.candidates if c.parent_id in ids and c.child_id in ids
        )
        for mode in ("llm_only", "hybrid"):
            req = ResolverRequest(
                request_id=f"{slug}-{mode}",
                document_id=overlay.document_id,
                mode=mode,
                input_hashes=overlay.input_hashes,
                nodes=nodes,
                candidates=candidates if mode == "hybrid" else (),
                section_context=(),
                continuation_candidates=(),
                target_node_ids=tuple(ids),
                allowed_node_ids=tuple(ids),
                allowed_evidence_ids=tuple(sorted({i for n in nodes for i in evidence_ids(n)})),
            )
            name = f"{slug}-{mode}.json"
            data = req.model_dump_json(indent=2).encode("utf8")
            (FOLDER / name).write_bytes(data)
            entries.append(
                dict(
                    file=name,
                    sha256=hashlib.sha256(data).hexdigest(),
                    input_token_upper_bound=token_upper_bound(req, config),
                    nodes=len(nodes),
                    source_overlay=str(overlay_path),
                    labels=[f.text_raw for f in nodes],
                    mode=mode,
                )
            )
    manifest = dict(
        provider="OpenAI Responses text only",
        model_id=config.model_id,
        requests=entries,
        max_calls=4,
        retries=0,
        max_output_tokens_per_call=1500,
        maximum_estimated_usd=4 * (25000 * 0.4 + 1500 * 1.6) / 1e6,
        sends_pdf_or_images=False,
        contains_numeric_value_cells=False,
        source="https://developers.openai.com/api/docs/pricing",
        status="prepared_not_executed",
    )
    (FOLDER / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf8"
    )
    print(FOLDER / "manifest.json")
    print([(e["file"], e["nodes"], e["input_token_upper_bound"]) for e in entries])


def execute():
    preflight = json.loads((FOLDER / "preflight.json").read_text(encoding="utf8"))
    for name in ("manifest", "config"):
        if (
            hashlib.sha256((FOLDER / f"{name}.json").read_bytes()).hexdigest()
            != preflight[f"{name}_sha256"]
        ):
            raise ValueError("PREFLIGHT_CONFIGURATION_CHANGED")
    manifest = json.loads((FOLDER / "manifest.json").read_text(encoding="utf8"))
    config = ResolverConfig.model_validate_json((FOLDER / "config.json").read_bytes()).model_copy(
        update={"allow_external": True}
    )
    if (FOLDER / "results.json").exists():
        raise ValueError("PILOT_ALREADY_ATTEMPTED_REQUIRES_NEW_APPROVAL")
    # Check every packet and exact HTTP body before the first network request.
    for item, checked in zip(manifest["requests"], preflight["requests"], strict=True):
        data = (FOLDER / item["file"]).read_bytes()
        req = ResolverRequest.model_validate_json(data)
        body = json.dumps(payload_for(req, config), ensure_ascii=False).encode("utf8")
        if (
            hashlib.sha256(data).hexdigest() != checked["request_sha256"]
            or hashlib.sha256(body).hexdigest() != checked["http_body_sha256"]
        ):
            raise ValueError("APPROVED_PACKET_CHANGED")
        if (
            hashlib.sha256(Path(item["source_overlay"]).read_bytes()).hexdigest()
            != checked["source_overlay_sha256"]
        ):
            raise ValueError("BASELINE_CHANGED")
    adapter = OpenAITextAdapter()
    results = []
    for item in manifest["requests"]:
        data = (FOLDER / item["file"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("APPROVED_PACKET_CHANGED")
        req = ResolverRequest.model_validate_json(data)
        proposal, call = adapter.resolve(req, config)
        source = SemanticOverlay.model_validate_json(Path(item["source_overlay"]).read_bytes())
        # Validator sees only allowed nodes/evidence, no possible out-of-packet IDs.
        ids = set(req.allowed_node_ids)
        scoped_candidates = tuple(
            c for c in source.candidates if c.parent_id in ids and c.child_id in ids
        )
        scoped_rules = tuple(
            d
            for d in source.decisions
            if d.proposal.parent_id in ids and d.proposal.child_id in ids
        )
        ds = validate_patches(
            req.nodes, scoped_candidates, proposal.patches, "text_llm", existing=scoped_rules
        )
        assert all(d.validation_status != "accepted" for d in ds)
        base = scoped_rules if req.mode == "hybrid" else ()
        nodes = assemble_nodes(req.nodes, tuple(base) + ds, uuid4(), config.model_id)
        result = dict(
            request_id=req.request_id,
            mode=req.mode,
            call=call.model_dump(mode="json"),
            proposal=proposal.model_dump(mode="json"),
            decisions=[d.model_dump(mode="json") for d in ds],
            nodes=[n.model_dump(mode="json") for n in nodes],
        )
        results.append(result)
        (FOLDER / "results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf8"
        )
        print(req.request_id, call.status, call.input_tokens, call.output_tokens, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-approved", action="store_true")
    args = parser.parse_args()
    execute() if args.execute_approved else prepare()
