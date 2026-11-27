"""Offline audit and exact HTTP-body export. Never calls a provider."""

# ruff: noqa: E402
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from doc_understanding.agents.semantic_resolver.models import (
    ResolverConfig,
    ResolverRequest,
    SemanticOverlay,
)
from doc_understanding.agents.semantic_resolver.providers import payload_for, token_upper_bound


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    folder = ROOT / "data/semantic-c1-live-pilot"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf8"))
    config = ResolverConfig.model_validate_json((folder / "config.json").read_bytes())
    assert not config.allow_external
    assert config.model_id == manifest["model_id"] == "gpt-4.1-mini-2025-04-14"
    assert config.max_calls == 4 and config.retries == 0 and config.concurrency == 1
    assert config.max_input_tokens == 25000 and config.max_output_tokens == 1500
    assert config.total_token_budget == 106000
    assert len(manifest["requests"]) == 4
    entries, pairs = [], {}
    key = os.environ.get("OPENAI_API_KEY")
    for item in manifest["requests"]:
        data = (folder / item["file"]).read_bytes()
        assert digest(data) == item["sha256"]
        req = ResolverRequest.model_validate_json(data)
        slug = req.request_id.rsplit("-", 1)[0]
        assert slug in ("income-integrated", "capital")
        source = SemanticOverlay.model_validate_json(Path(item["source_overlay"]).read_bytes())
        originals = {f.node_id: f for f in source.features}
        assert req.document_id == source.document_id
        assert [n.text_raw for n in req.nodes] == item["labels"]
        assert len(req.nodes) == (6 if slug == "income-integrated" else 5)
        for n in req.nodes:
            assert not n.value_cell_ids
            assert n == originals[n.node_id].model_copy(update={"value_cell_ids": ()})
            assert n.axis == "row" and n.page_number == 1
            assert all(r.document_id == req.document_id for r in n.source_refs)
        assert not req.section_context and not req.continuation_candidates
        ids = {n.node_id for n in req.nodes}
        assert set(req.allowed_node_ids) == set(req.target_node_ids) == ids
        assert all(c.parent_id in ids and c.child_id in ids for c in req.candidates)
        if req.mode == "llm_only":
            assert not req.candidates
        pairs.setdefault(slug, []).append(req)
        body = json.dumps(payload_for(req, config), ensure_ascii=False).encode("utf8")
        assert not key or key.encode() not in body
        decoded = body.decode("utf8")
        assert all(
            x not in decoded
            for x in ("input_image", "input_file", "data:image", "%PDF-", "C:\\\\Users")
        )
        assert (
            token_upper_bound(req, config)
            == item["input_token_upper_bound"]
            <= config.max_input_tokens
        )
        name = item["file"].replace(".json", ".http-body.json")
        (folder / name).write_bytes(body)
        entries.append(
            dict(
                request_id=req.request_id,
                request_sha256=digest(data),
                http_body_file=name,
                http_body_sha256=digest(body),
                source_overlay_sha256=digest(Path(item["source_overlay"]).read_bytes()),
                labels=item["labels"],
                candidate_count=len(req.candidates),
                input_token_upper_bound=token_upper_bound(req, config),
            )
        )
    for a, b in pairs.values():
        assert a.nodes == b.nodes and a.allowed_evidence_ids == b.allowed_evidence_ids
    result = dict(
        status="preflight_passed_awaiting_user_approval",
        actual_api_calls=0,
        config_sha256=digest((folder / "config.json").read_bytes()),
        manifest_sha256=digest((folder / "manifest.json").read_bytes()),
        api_key_present=bool(key),
        maximum_estimated_usd=manifest["maximum_estimated_usd"],
        numeric_cell_values_sent=False,
        value_cell_ids_sent=False,
        pdf_or_images_sent=False,
        matching_input_features_between_modes=True,
        requests=entries,
    )
    (folder / "preflight.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8"
    )
    print(json.dumps({k: v for k, v in result.items() if k != "requests"}))


if __name__ == "__main__":
    main()
