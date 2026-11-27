"""Offline validation of the existing eight frozen payloads. No provider calls."""

# ruff: noqa: E402
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/namwon-holdout"
PLAN = OUT / "pilot-plan"
sys.path.insert(0, str(ROOT / "src"))
from doc_understanding.agents.semantic_resolver.models import (
    ResolverConfig,
    ResolverRequest,
    SemanticOverlay,
)
from doc_understanding.agents.semantic_resolver.providers import payload_for, token_upper_bound
from doc_understanding.agents.semantic_resolver.candidates import evidence_ids
from doc_understanding.agents.semantic_resolver.engine import digest
from doc_understanding.domain.models import DocumentIR
from doc_understanding.parser.physical.physical_models import PhysicalTableIR
from doc_understanding.parser.structure.models import StructuredTableIR


def read(path):
    return json.loads(path.read_text(encoding="utf8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest = read(PLAN / "manifest.json")
    config = ResolverConfig.model_validate_json((PLAN / "config.json").read_bytes())
    assert not config.allow_external
    assert config.model_id == manifest["model_id"] == "gpt-4.1-mini-2025-04-14"
    assert (
        config.max_calls,
        config.retries,
        config.concurrency,
        config.max_input_tokens,
        config.max_output_tokens,
        config.total_token_budget,
    ) == (8, 0, 1, 60000, 3500, 508000)
    assert len(manifest["requests"]) == 8
    assert sha(OUT / "source-annotations.json") == manifest["golden_sha256"]
    frozen = read(OUT / "frozen-baseline.json")
    for path, expected in frozen["files"].items():
        assert sha(ROOT / path) == expected, path
    run = next(r for r in read(OUT / "runs.json") if r["slug"] == "audit-native")
    overlay_path = Path(run["semantic_run"]) / "semantic-overlay.json"
    overlay = SemanticOverlay.model_validate_json(overlay_path.read_bytes())
    originals = {f.node_id: f for f in overlay.features}
    assert not overlay.calls
    doc = DocumentIR.model_validate_json(
        (Path(run["physical_run"]) / "document-ir.json").read_bytes()
    )
    assert digest(doc) == overlay.input_hashes["document"]
    physical = {}
    for path in (Path(run["physical_run"]) / "physical").glob("page-*-table-*.json"):
        table = PhysicalTableIR.model_validate_json(path.read_bytes())
        assert digest(table) == overlay.input_hashes[f"physical:{table.table_id}"]
        physical.update({c.cell_id: c for c in table.cells})
    for data in read(Path(run["semantic_run"]) / "structured-before.json"):
        table = StructuredTableIR.model_validate(data)
        assert digest(table) == overlay.input_hashes[f"structured:{table.table_id}"]
    registry = next(r for r in read(OUT / "registry.json") if r["slug"] == "audit-native")
    assert sha(Path(registry["path"])) == registry["sha256"]
    entries, pairs = [], {}
    key = os.environ.get("OPENAI_API_KEY")
    for item in manifest["requests"]:
        path = PLAN / item["file"]
        assert sha(path) == item["sha256"]
        req = ResolverRequest.model_validate_json(path.read_bytes())
        assert req.document_id == overlay.document_id
        assert item["page"] in (9, 42, 43, 51)
        assert len(req.nodes) == {9: 6, 42: 13, 43: 17, 51: 18}[item["page"]]
        assert not req.section_context and not req.continuation_candidates
        for n in req.nodes:
            assert n == originals[n.node_id].model_copy(update={"value_cell_ids": ()})
            assert n.axis == "row" and n.page_number == item["page"]
            assert all(cid in physical for cid in n.source_cell_ids)
            assert all(physical[cid].original_text == n.text_raw for cid in n.source_cell_ids)
        ids = {n.node_id for n in req.nodes}
        assert set(req.allowed_node_ids) == set(req.target_node_ids) == ids
        assert set(req.allowed_evidence_ids) == {i for n in req.nodes for i in evidence_ids(n)}
        scoped = tuple(c for c in overlay.candidates if c.parent_id in ids and c.child_id in ids)
        assert req.candidates == (scoped if req.mode == "hybrid" else ())
        for k, v in req.input_hashes.items():
            assert overlay.input_hashes[k] == v
        body = json.dumps(payload_for(req, config), ensure_ascii=False).encode("utf8")
        assert hashlib.sha256(body).hexdigest() == item["body_sha256"]
        body_file = path.with_name(path.stem + ".http-body.json")
        assert body_file.read_bytes() == body
        assert not key or key.encode() not in body
        assert not any(
            x in body
            for x in (b"input_image", b"input_file", b"data:image", b"%PDF-", b"golden-review")
        )
        assert (
            token_upper_bound(req, config)
            == item["input_token_upper_bound"]
            <= config.max_input_tokens
        )
        pairs.setdefault(item["page"], []).append(req)
        entries.append(
            dict(
                file=item["file"],
                request_sha256=item["sha256"],
                body_sha256=item["body_sha256"],
                page=item["page"],
                mode=req.mode,
                labels=item["labels"],
                node_count=len(req.nodes),
                input_token_upper_bound=token_upper_bound(req, config),
                max_output_tokens=config.max_output_tokens,
            )
        )
    for a, b in pairs.values():
        assert a.nodes == b.nodes and a.allowed_evidence_ids == b.allowed_evidence_ids
    ids = {n.node_id for reqs in pairs.values() for n in reqs[0].nodes}
    gold = read(OUT / "golden-review.json")
    assert gold["metrics"]["source_annotation_sha256"] == manifest["golden_sha256"]
    scoreable = [
        c
        for c in gold["cases"]
        if not c["negative"]
        and c.get("relation_type", "parent_child") == "parent_child"
        and c["parent_anchor"]["node_id"] in ids
        and c["child_anchor"]["node_id"] in ids
    ]
    by_page = {
        str(p): {
            s: sum(c["status"] == s and c["page"] == p for c in scoreable)
            for s in ("correct", "missing_parent", "wrong_parent_or_relation")
        }
        for p in pairs
    }
    input_bound = sum(e["input_token_upper_bound"] for e in entries)
    report = dict(
        status="preflight_passed_awaiting_user_approval",
        actual_calls=0,
        manifest_sha256=sha(PLAN / "manifest.json"),
        config_sha256=sha(PLAN / "config.json"),
        golden_sha256=manifest["golden_sha256"],
        baseline_overlay_path=str(overlay_path),
        baseline_overlay_sha256=sha(overlay_path),
        evaluation_review_sha256=sha(OUT / "golden-review.json"),
        unchanged_frozen_files=len(frozen["files"]),
        unchanged_physical_cells=len(physical),
        source_pdf_sha256=registry["sha256"],
        model_id=config.model_id,
        api_key_present=bool(key),
        numeric_value_cells_sent=False,
        pdf_or_images_sent=False,
        selected_parent_targets=len(scoreable),
        excluded_parent_targets=26 - len(scoreable),
        baseline_by_page=by_page,
        evaluated_case_ids=[c["id"] for c in scoreable],
        input_upper_bound_total=input_bound,
        frozen_payload_cost_upper_estimate_usd=(input_bound * 0.4 + 8 * 3500 * 1.6) / 1e6,
        configured_cost_ceiling_usd=manifest["maximum_estimated_usd"],
        requests=entries,
    )
    (PLAN / "preflight.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8"
    )
    print(json.dumps({k: v for k, v in report.items() if k != "requests"}, ensure_ascii=True))


if __name__ == "__main__":
    main()
