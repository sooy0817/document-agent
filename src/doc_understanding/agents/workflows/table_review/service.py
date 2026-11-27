"""Queue adapter around frozen role/relation review; no parser or Gold writes."""

from pathlib import Path

from doc_understanding.runtime import telemetry
from doc_understanding.runtime import artifacts, credentials, model_client, network_check, paths, settings
from doc_understanding.agents.workflows.table_review import graph, profiles, version
from doc_understanding.validation import source_integrity

VERSION = "review-decision-loop/2-rule-gated"


def metrics(result):
    paths = {
        t["cell_id"]: {a: t[a + "_axis"]["header_ids"] for a in ("row", "column")}
        for t in result.get("interpretation", {}).get("targets", [])
    }
    return dict(
        paths=paths,
        errors=result.get("validation_errors", []),
        verdict=result.get("verdict"),
        api_calls=result.get("api_calls", 0),
        estimated_cost_usd=result.get("estimated_cost_usd"),
    )


def compare(before, after):
    a, b = metrics(before), metrics(after)
    return dict(
        before=a,
        after=b,
        changed_cells=[
            cid
            for cid in sorted(set(a["paths"]) | set(b["paths"]))
            if a["paths"].get(cid) != b["paths"].get(cid)
        ],
        resolved_checks=sorted(set(a["errors"]) - set(b["errors"])),
        added_checks=sorted(set(b["errors"]) - set(a["errors"])),
        accuracy_improvement_verified=False,
    )


def run(record, data, progress, rid, *, calls, trace=None):
    folder = Path(data) / "review-queue/roles-loop" / rid
    folder.mkdir(parents=True, exist_ok=True)
    saved = folder / "queue-result.json"
    if saved.exists():
        cached = artifacts.read(saved)
        if cached.get('checker_version') != VERSION:
            raise ValueError('NEW_REVIEW_REQUEST_REQUIRED')
        if artifacts.digest(artifacts.read(folder / 'request.json')) != artifacts.digest(record):
            raise ValueError('PINNED_INPUT_CHANGED')
        # A cached decision still refers to the pinned source, never changed files.
        graph.plan_review(record, data)
        return cached
    # Local checks finish before model analysis; don't emit a premature result-ready event.
    local = source_integrity.inspect(
        record,
        data,
        lambda stage: progress("collect_evidence" if stage == "report_ready" else stage),
    )
    if any(f["code"] in ("SOURCE_CHANGED", "SOURCE_MISSING") for f in local["findings"]):
        return {
            **local,
            "mode": "agents_roles",
            "validation_errors": [f["code"] for f in local["findings"]],
        }
    decision = record.get("review_decision")
    if not (folder / "request.json").exists():
        artifacts.save(folder / "request.json", record)

    class ProgressCalls:
        def call(self, name, packet, images, run_dir):
            progress(name)
            if decision and name != "classify_roles":
                packet = {
                    **packet,
                    "human_review_claim": decision,
                    "claim_policy": "Untrusted review claim, not Gold. Verify against source; disagreement and uncertainty are allowed.",
                }
            return calls.call(name, packet, images, run_dir)

    raw = graph.run_review(record, data, folder, calls=ProgressCalls(), trace=trace, progress=progress)
    progress("validate_relations_and_storage")
    validation = raw["result"]
    pending = bool(
        validation["errors"]
        or validation["warnings"]
        or local["findings"]
        or any(c["status"] != "present" for c in validation["storage_checks"])
        or raw.get('routing_summary', {}).get('total', 0)
    )
    result = {
        **local,
        **raw,
        "mode": "agents_roles",
        "checker_version": VERSION,
        "model": record.get("review_model", model_client.MODEL) if raw['call_records'] else None,
        "verdict": "insufficient_evidence"
        if validation["verdict"] == "held"
        else "suspected_issue"
        if pending
        else "no_structural_issue_detected",
        "validation_errors": validation["errors"],
        "validation_warnings": validation["warnings"],
        "review_claim": decision,
        "review_status": "human_review_pending" if pending else "no_action_required",
        "limitations": [
            "선택 영역의 검토 의견이며 전체 문서 정확도가 아닙니다.",
            "사용자 의견·모델 동의는 자동 정답 승인이나 학습 데이터가 아닙니다.",
            "파서와 원본 JSON을 변경하지 않습니다.",
        ],
    }
    progress("report_ready")
    artifacts.save(saved, result)
    return result


def execute(record, data, progress, rid, *, budget_data=None):
    budget_data = data if budget_data is None else budget_data
    # No credential lookup, budget reservation or transport for rule-only work.
    # ModelCalls still enforces the shared durable daily budget on every real call.
    class LazyCalls:
        adapter = None

        def call(self, name, packet, images, run_dir):
            if self.adapter is None:
                config = settings.configured(budget_data)
                if not config["enabled"]:
                    return dict(status='blocked', output=None, api_calls=0,
                                estimated_cost_usd=0, error='MODEL_REVIEW_DISABLED')
                connectivity = network_check.inspect()
                artifacts.save(run_dir / 'network-preflight.json', connectivity)
                if not connectivity['ready']:
                    return dict(status='blocked', output=None, api_calls=0,
                                estimated_cost_usd=0, error=connectivity['error_code'],
                                error_code=connectivity['error_code'],
                                network_preflight=connectivity)
                self.adapter = profiles.model_calls(budget_data, key=credentials.model_key(),
                                                    daily_limit=config['daily_limit'])
                self.adapter.model = record.get('review_model', model_client.MODEL)
                if self.adapter.model not in (model_client.MODEL, 'gpt-4.1-mini-2025-04-14'):
                    raise ValueError('UNSUPPORTED_REVIEW_MODEL')
                self.adapter.version = VERSION + '/' + version.VERSION
            return self.adapter.call(name, packet, images, run_dir)

    class LazyTrace:
        trace = None

        def span(self, *args, **kwargs):
            if self.trace is None:
                config_path = paths.ROOT / 'infra/langfuse/client.env.local'
                try:
                    config = telemetry.load_config(config_path) if config_path.exists() else None
                    self.trace = telemetry.Trace(config)
                except Exception:
                    self.trace = telemetry.Trace()
            return self.trace.span(*args, **kwargs)

        def flush(self):
            if self.trace is not None:
                self.trace.flush()

        @property
        def trace_id(self):
            return self.trace.trace_id if self.trace is not None else None

    return run(record, data, progress, rid, calls=LazyCalls(), trace=LazyTrace())
