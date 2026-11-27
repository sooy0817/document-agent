"""Offline bounded requests only. This script cannot make API calls."""

# ruff: noqa: E402
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from doc_understanding.agents.semantic_resolver.models import (
    SemanticOverlay,
    ResolverConfig,
    ResolverRequest,
)
from doc_understanding.agents.semantic_resolver.candidates import evidence_ids
from doc_understanding.agents.semantic_resolver.providers import payload_for, token_upper_bound


def main():
    out = ROOT / "data/namwon-holdout"
    run = json.loads((out / "runs.json").read_text(encoding="utf8"))[0]
    overlay = SemanticOverlay.model_validate_json(
        (Path(run["semantic_run"]) / "semantic-overlay.json").read_bytes()
    )
    folder = out / "pilot-plan"
    folder.mkdir(exist_ok=True)
    config = ResolverConfig(
        model_id="gpt-4.1-mini-2025-04-14",
        max_calls=8,
        retries=0,
        concurrency=1,
        timeout_seconds=30,
        max_input_tokens=60000,
        max_output_tokens=3500,
        total_token_budget=508000,
        max_nodes_per_request=24,
        allow_external=False,
        input_price_per_million=0.4,
        output_price_per_million=1.6,
    )
    (folder / "config.json").write_text(config.model_dump_json(indent=2), encoding="utf8")
    entries = []
    for page, role in [
        (9, "normal_control"),
        (42, "hard_subtotal"),
        (43, "hard_group_and_subtotal"),
        (51, "hard_group_and_subtotal"),
    ]:
        nodes = tuple(
            f.model_copy(update={"value_cell_ids": ()})
            for f in overlay.features
            if f.page_number == page and f.axis == "row"
        )
        if page == 9:
            nodes = nodes[:6]
        ids = {n.node_id for n in nodes}
        candidates = tuple(
            c for c in overlay.candidates if c.parent_id in ids and c.child_id in ids
        )
        assert all(
            not any(
                ch.isdigit()
                for ch in n.text_raw.replace("1.", "")
                .replace("2.", "")
                .replace("3.", "")
                .replace("(1)", "")
            )
            for n in nodes
        )
        for mode in ("llm_only", "hybrid"):
            req = ResolverRequest(
                request_id=f"namwon-p{page:03}-{mode}",
                document_id=overlay.document_id,
                mode=mode,
                input_hashes={
                    k: v
                    for k, v in overlay.input_hashes.items()
                    if k == "document" or k.split(":", 1)[-1] in {n.table_id for n in nodes}
                },
                nodes=nodes,
                candidates=candidates if mode == "hybrid" else (),
                section_context=(),
                continuation_candidates=(),
                target_node_ids=tuple(n.node_id for n in nodes),
                allowed_node_ids=tuple(n.node_id for n in nodes),
                allowed_evidence_ids=tuple(sorted({i for n in nodes for i in evidence_ids(n)})),
            )
            body = json.dumps(payload_for(req, config), ensure_ascii=False).encode("utf8")
            bound = token_upper_bound(req, config)
            assert bound <= config.max_input_tokens
            file = req.request_id + ".json"
            (folder / file).write_text(req.model_dump_json(indent=2), encoding="utf8")
            (folder / (req.request_id + ".http-body.json")).write_bytes(body)
            entries.append(
                dict(
                    file=file,
                    sha256=hashlib.sha256((folder / file).read_bytes()).hexdigest(),
                    body_sha256=hashlib.sha256(body).hexdigest(),
                    page=page,
                    role=role,
                    mode=mode,
                    node_count=len(nodes),
                    labels=[n.text_raw for n in nodes],
                    input_token_upper_bound=bound,
                )
            )
    manifest = dict(
        status="prepared_only_not_authorized",
        actual_calls=0,
        model_id=config.model_id,
        price_basis="Previous approved pilot rates: input 0.40/M, output 1.60/M; not rechecked externally in this no-transmission task",
        maximum_estimated_usd=8 * (60000 * 0.4 + 3500 * 1.6) / 1e6,
        requests=entries,
        max_calls=8,
        retries=0,
        send_pdf=False,
        send_images=False,
        send_numeric_values=False,
        golden_file="../source-annotations.json",
        golden_sha256=hashlib.sha256((out / "source-annotations.json").read_bytes()).hexdigest(),
        evaluation="Score only annotated targets and paths; unannotated new edges require source review. Same features and gold across all modes. Record explicit abstentions, silent omissions, recovered/misparented edges and net delta. No Vision required for these windows.",
    )
    (folder / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf8"
    )
    print([(e["file"], e["node_count"], e["input_token_upper_bound"]) for e in entries])


if __name__ == "__main__":
    main()
