"""Single approved, bounded experiment; frozen production code remains unchanged."""

# ruff: noqa: E402
import argparse
import hashlib
import json
import sys
import urllib.error
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from doc_understanding.agents.semantic_resolver.models import (
    ResolverConfig,
    ResolverRequest,
    SemanticOverlay,
)
from doc_understanding.agents.semantic_resolver.providers import (
    OpenAITextAdapter,
    payload_for,
    http_transport,
)
from doc_understanding.agents.semantic_resolver.validator import validate_patches
from doc_understanding.agents.semantic_resolver.engine import assemble_nodes

PLAN = ROOT / "data/namwon-holdout/pilot-plan"
OUT = ROOT / "data/namwon-holdout/live-hardcase"


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read(p):
    return json.loads(p.read_text(encoding="utf8"))


def save(p, value):
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-approved", action="store_true")
    if not parser.parse_args().execute_approved:
        raise SystemExit("Explicit approved execution flag required")
    checked = read(PLAN / "preflight.json")
    manifest = read(PLAN / "manifest.json")
    for name in ("manifest", "config"):
        assert sha(PLAN / f"{name}.json") == checked[f"{name}_sha256"]
    assert sha(PLAN.parent / "source-annotations.json") == checked["golden_sha256"]
    assert sha(PLAN.parent / "golden-review.json") == checked["evaluation_review_sha256"]
    source_path = Path(checked["baseline_overlay_path"])
    assert sha(source_path) == checked["baseline_overlay_sha256"]
    source = SemanticOverlay.model_validate_json(source_path.read_bytes())
    config = ResolverConfig.model_validate_json((PLAN / "config.json").read_bytes()).model_copy(
        update={"allow_external": True}
    )
    assert (
        config.max_calls == 8 and config.retries == 0 and manifest["maximum_estimated_usd"] < 0.25
    )
    requests = []
    for item in manifest["requests"]:
        p = PLAN / item["file"]
        assert sha(p) == item["sha256"]
        req = ResolverRequest.model_validate_json(p.read_bytes())
        body = json.dumps(payload_for(req, config), ensure_ascii=False).encode("utf8")
        assert hashlib.sha256(body).hexdigest() == item["body_sha256"]
        requests.append(req)
    OUT.mkdir(exist_ok=True)
    # Exclusive creation prevents accidental repetition even after an interrupted attempt.
    with (OUT / "execution-lock.json").open("x", encoding="utf8") as f:
        json.dump(
            dict(
                authorization="User approved preceding eight-request preflight: 응",
                preflight_sha256=sha(PLAN / "preflight.json"),
                max_calls=8,
                max_usd=0.25,
            ),
            f,
        )
    results = []
    ledger = []

    def transport(body, key, timeout):
        if len(ledger) >= 8:
            raise ValueError("PILOT_CALL_CAP")
        item = dict(
            attempt=len(ledger) + 1,
            state="attempt_started",
            body_sha256=hashlib.sha256(
                json.dumps(body, ensure_ascii=False).encode("utf8")
            ).hexdigest(),
        )
        ledger.append(item)
        save(OUT / "transport-ledger.json", ledger)
        try:
            response = http_transport(body, key, timeout)
            # Preserve incomplete responses for auditing, never silently parse partial patches.
            save(OUT / f"response-{len(ledger):02}.json", response)
            item.update(state="response_received", provider_status=response.get("status"))
            return response
        except urllib.error.HTTPError as error:
            item.update(state="http_error", http_status=error.code)
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            item.update(state="transport_error", exception_type=type(error).__name__)
            raise
        finally:
            save(OUT / "transport-ledger.json", ledger)

    adapter = OpenAITextAdapter(transport=transport)
    for req in requests:
        proposal, call = adapter.resolve(req, config)
        ids = set(req.allowed_node_ids)
        candidates = tuple(c for c in source.candidates if c.parent_id in ids and c.child_id in ids)
        rules = tuple(
            d
            for d in source.decisions
            if d.proposal.parent_id in ids and d.proposal.child_id in ids
        )
        decisions = validate_patches(
            req.nodes, candidates, proposal.patches, "text_llm", existing=rules
        )
        assert all(d.validation_status != "accepted" for d in decisions)
        nodes = assemble_nodes(
            req.nodes, (rules if req.mode == "hybrid" else ()) + decisions, uuid4(), config.model_id
        )
        results.append(
            dict(
                request_id=req.request_id,
                mode=req.mode,
                call=call.model_dump(mode="json"),
                proposal=proposal.model_dump(mode="json"),
                decisions=[d.model_dump(mode="json") for d in decisions],
                nodes=[n.model_dump(mode="json") for n in nodes],
            )
        )
        save(OUT / "results.json", results)
        print(
            req.request_id,
            call.status,
            call.error_code,
            call.input_tokens,
            call.output_tokens,
            flush=True,
        )
        if call.error_code == "TRANSPORT_ERROR":
            break
    assert sha(source_path) == checked["baseline_overlay_sha256"]
    assert sha(PLAN.parent / "source-annotations.json") == checked["golden_sha256"]


if __name__ == "__main__":
    main()
