"""teams / evidence_review / graph."""

import hashlib
from pathlib import Path

from pydantic import Field, create_model

from doc_understanding.agents.roles import diagnostic_analyst, diagnostic_judge, source_reviewer
from doc_understanding.agents.roles import judge as agents_judge
from doc_understanding.runtime import telemetry
from doc_understanding.runtime import artifacts, model_client
from doc_understanding.agents.workflows.evidence_review import state as evidence_review_state
from doc_understanding.agents.workflows.evidence_review import version
from doc_understanding.tools import evidence, evidence_expansion, relation_index
from doc_understanding.validation import review, source_integrity, source_review


def run_review(record, data, progress, rid, *, calls, trace=None):
    from langgraph.graph import END, START, StateGraph

    trace = trace or telemetry.Trace()
    folder = Path(data) / record["snapshot"]["job_id"]
    run_dir = Path(data) / "review-queue" / "agents-v2" / rid
    run_dir.mkdir(parents=True, exist_ok=True)
    ctx = {}
    records = []

    def pin(name, value):
        path = run_dir / name
        if path.exists():
            if artifacts.digest(artifacts.read(path)) != artifacts.digest(value):
                raise ValueError("PINNED_INPUT_CHANGED")
        else:
            artifacts.save(path, value)

    def collect(state):
        progress("collect_evidence")
        with trace.span("collect_evidence", dict(run_id=rid)) as box:
            ctx["local"] = source_integrity.inspect(record, data, lambda _: None)
            try:
                packet, images = evidence.collect_evidence(record, data)
                index = relation_index.RelationIndex(
                    artifacts.read(folder / "document.json"),
                    artifacts.read(folder / "nested.json")
                    if (folder / "nested.json").exists()
                    else {"cells": []},
                )
                ids = {e["id"] for e in packet["evidence"]}
                packet["relation_inventory"] = [
                    e for e in index.edges if e["source_id"] in ids or e["target_id"] in ids
                ]
                packet["relation_inventory_scope"] = (
                    "All stored relations touching provided IDs; present does not imply correct."
                )
                pin("evidence.json", packet)
                ctx.update(packet=packet, images=images, index=index, initial_packet=packet)
                for n, i in enumerate(images):
                    p = run_dir / f"source-{n}.png"
                    if not p.exists():
                        p.write_bytes(i["bytes"])
            except (ValueError, FileNotFoundError) as exc:
                ctx["blocked"] = type(exc).__name__
            box["output"] = dict(
                status="blocked" if ctx.get("blocked") else "ready",
                errors=[ctx["blocked"]] if ctx.get("blocked") else [],
            )
        return {}

    def call(role, packet):
        progress(role)
        with trace.span(role, dict(run_id=rid, mode=version.VERSION), generation=True) as box:
            result = {
                "source_review": source_reviewer.run,
                "analyst_v2": diagnostic_analyst.run,
                "reanalysis": diagnostic_analyst.reanalyze,
                "judge_v2": diagnostic_judge.run,
            }[role](calls, packet, ctx["images"], run_dir)
            records.append(result)
            out = result.get("output") or {}
            box["output"] = dict(
                status=result["status"],
                reason=out.get("summary"),
                usage=result.get("usage") or {},
                estimated_cost_usd=result.get("estimated_cost_usd"),
                candidates=[
                    dict(
                        status=i.get("verdict", i.get("certainty")),
                        reason=i.get("reason", i.get("observation")),
                        source_refs=[c["source_id"] for c in i.get("citations", [])],
                    )
                    for i in out.get("assessments", out.get("issues", []))
                ],
            )
        return result.get("output")

    def source(state):
        return (
            dict(source=call("source_review", source_reviewer.blind_packet(ctx["packet"])))
            if not ctx.get("blocked")
            else {}
        )

    def analyze(state):
        if ctx.get("blocked"):
            return {}
        a = call("analyst_v2", ctx["packet"])
        return dict(initial=a, analysis=a, checks=relation_index.lookup_claims(a, ctx["index"]))

    def route(state):
        a = state.get("analysis")
        contradicted = (
            any(c["contradiction"] for c in relation_index.check_stored_claims(a, ctx["index"]))
            if ctx.get("index")
            else False
        )
        return (
            "expand_evidence"
            if a
            and (a["requests"] or any(c["contradiction"] for c in state["checks"]) or contradicted)
            else "judge_v2"
        )

    def expand_node(state):
        progress("expand_evidence")
        with trace.span("readonly_tools", dict(run_id=rid)) as box:
            qs = state["analysis"]["requests"][:2]
            if not qs:
                qs = [
                    dict(
                        tool="lookup_relation",
                        source_id=c["source_id"],
                        target_id=c["target_id"],
                        relation=c["relation"],
                        page=None,
                        reason="missing claim contradicts stored relation",
                    )
                    for c in state["checks"]
                    if c["contradiction"]
                ][:2]
            if not qs:
                qs = [
                    dict(
                        tool="lookup_relation",
                        source_id=c["claim"]["source_id"],
                        target_id=c["claim"]["target_id"],
                        relation=c["claim"]["relation"],
                        page=None,
                        reason="stored relation assertion contradicts snapshot",
                    )
                    for c in relation_index.check_stored_claims(state["analysis"], ctx["index"])
                    if c["contradiction"]
                ][:2]
            # Detect source changes before any follow-up read/model call.
            if any(
                hashlib.sha256((folder / name).read_bytes()).hexdigest() != record["snapshot"][key]
                for name, key in [
                    ("document.json", "canonical_sha256"),
                    ("input.pdf", "source_pdf_sha256"),
                ]
            ):
                ctx["blocked"] = "SOURCE_CHANGED"
                return dict(tools=[])
            packet, images, tools = evidence_expansion.expand(
                ctx["packet"], ctx["images"], qs, ctx["index"], folder
            )
            if packet is None:
                ctx["blocked"] = "EXPANDED_EVIDENCE_BUDGET_EXCEEDED"
            else:
                ctx.update(packet=packet, images=images)
                pin("expanded-evidence.json", packet)
            pin("tool-results.json", tools)
            box["output"] = dict(
                candidates=[
                    dict(
                        method=t["request"]["tool"],
                        status=t["status"],
                        source_refs=t.get("source_refs", []),
                        reason=t["request"]["reason"],
                    )
                    for t in tools
                ]
            )
        return dict(tools=tools)

    def reanalyze(state):
        if ctx.get("blocked"):
            return {}
        a = call(
            "reanalysis",
            {
                **ctx["packet"],
                "initial_analysis": state["initial"],
                "relation_checks": state["checks"],
                "expansion_exhausted": True,
            },
        )
        return dict(analysis=a, checks=relation_index.lookup_claims(a, ctx["index"]))

    def judge(state):
        if ctx.get("blocked") or not state.get("analysis"):
            return {}
        issues = state["analysis"]["issues"]
        if isinstance(calls, model_client.ModelCalls):
            # Strict schema requires the exact number of supplied issues, including zero.
            bounded = create_model(
                "BoundedJudgement",
                __base__=agents_judge.Judgement,
                assessments=(
                    list[agents_judge.Assessment],
                    Field(min_length=len(issues), max_length=len(issues)),
                ),
            )
            calls.profiles["judge_v2"] = (diagnostic_judge.JUDGE, bounded)
        return dict(
            judge=call(
                "judge_v2",
                {
                    **ctx["packet"],
                    "analyst_candidate": state["analysis"],
                    "blind_source_review": state.get("source"),
                    "relation_checks": state["checks"],
                    "stored_claim_checks": relation_index.check_stored_claims(
                        state["analysis"], ctx["index"]
                    ),
                },
            )
        )

    def validate(state):
        progress("validate_review")
        with trace.span("validate_review", dict(run_id=rid)) as box:
            errors = review.check_outputs(
                ctx.get("packet", {"evidence": []}), state.get("analysis"), state.get("judge")
            )
            errors += source_review.check_source(
                ctx.get("initial_packet", {"evidence": []}), state.get("source")
            )
            errors += [
                "MISSING_CLAIM_CONTRADICTS_STORED_RELATION:" + c["issue_id"]
                for c in state.get("checks", [])
                if c["contradiction"]
            ]
            if ctx.get("index"):
                errors += [
                    "STORED_RELATION_ASSERTION_CONTRADICTED"
                    for c in relation_index.check_stored_claims(state.get("analysis"), ctx["index"])
                    if c["contradiction"]
                ]
            if ctx.get("blocked"):
                errors.append(ctx["blocked"])
            if (state.get("analysis") or {}).get("requests"):
                errors.append("EXTRA_EVIDENCE_REQUEST_UNRESOLVED")
            box["output"] = dict(errors=errors, semantic_status="human_review_pending")
        return dict(errors=errors)

    def report(state):
        progress("report_ready")
        a = state.get("analysis")
        j = state.get("judge")
        s = state.get("source")
        unresolved = bool(
            state["errors"]
            or ctx.get("packet", {}).get("omitted_ids")
            or any(
                t["status"] in ("unknown", "not_found_in_snapshot") or t.get("omitted_ids")
                for t in state.get("tools", [])
            )
        )
        if a and j and s:
            unresolved |= bool(
                a["missing_evidence"]
                or j["missing_evidence"]
                or j["additional_concerns"]
                or s["missing_evidence"]
                or any(i["certainty"] == "insufficient_evidence" for i in a["issues"])
                or any(i["verdict"] != "agree" for i in j["assessments"])
            )
        costs = [r.get("estimated_cost_usd") for r in records]
        result = {
            **ctx["local"],
            "mode": "agents_v2",
            "checker_version": version.VERSION,
            "analysis": a,
            "initial_analysis": state.get("initial"),
            "judge": j,
            "source_review": s,
            "relation_checks": state.get("checks", []),
            "initial_relation_checks": relation_index.lookup_claims(
                state.get("initial"), ctx["index"]
            )
            if ctx.get("index")
            else [],
            "tool_results": state.get("tools", []),
            "expansion_count": int("tools" in state),
            "stored_claim_checks": relation_index.check_stored_claims(a, ctx["index"])
            if ctx.get("index")
            else [],
            "validation_errors": state["errors"],
            "verdict": "insufficient_evidence"
            if unresolved
            else "suspected_issue"
            if a and a["issues"]
            else "no_structural_issue_detected",
            "api_calls": sum(r.get("api_calls", 0) for r in records),
            "estimated_cost_usd": None if any(c is None for c in costs) else sum(costs),
            "call_records": [
                {k: v for k, v in r.items() if k not in ("response_text", "output")}
                for r in records
            ],
            "trace_id": trace.trace_id,
            "trace_warnings": trace.warnings,
            "evidence_scope": dict(
                scope=ctx.get("packet", {}).get("scope"),
                omitted_ids=ctx.get("packet", {}).get("omitted_ids", []),
            ),
            "limitations": [
                "저장된 관계 존재는 원본 의미 정확성의 승인이 아닙니다.",
                "원본 검토는 사용자 주장·의미 후보·분석 의견을 보지 않지만 추출 셀을 위치 앵커로 사용합니다.",
                "같은 모델의 별도 호출이며 원본·Gold·파서를 자동 수정하지 않습니다.",
            ],
        }
        if not (run_dir / "result.json").exists():
            artifacts.save(run_dir / "result.json", result)
        return dict(result=result)

    graph = StateGraph(evidence_review_state.State)
    for name, node in [
        ("collect_evidence", collect),
        ("source_review", source),
        ("analyst_v2", analyze),
        ("expand_evidence", expand_node),
        ("reanalysis", reanalyze),
        ("judge_v2", judge),
        ("validate_review", validate),
        ("report", report),
    ]:
        graph.add_node(name, node)
    graph.add_edge(START, "collect_evidence")
    graph.add_edge("collect_evidence", "source_review")
    graph.add_edge("source_review", "analyst_v2")
    graph.add_conditional_edges("analyst_v2", route)
    graph.add_edge("expand_evidence", "reanalysis")
    graph.add_edge("reanalysis", "judge_v2")
    graph.add_edge("judge_v2", "validate_review")
    graph.add_edge("validate_review", "report")
    graph.add_edge("report", END)
    with trace.span("document-review-accuracy", dict(run_id=rid, mode=version.VERSION)) as root:
        if trace.client:
            try:
                trace.trace_id = trace.client.get_current_trace_id()
            except Exception:
                pass
        result = graph.compile().invoke({})["result"]
        root["output"] = dict(
            status=result["verdict"],
            errors=result["validation_errors"],
            estimated_cost_usd=result["estimated_cost_usd"],
        )
    trace.flush()
    if trace.client and trace.trace_id:
        try:
            result["trace_url"] = trace.client.get_trace_url(trace_id=trace.trace_id)
        except Exception:
            trace.warnings.append("LANGFUSE_TRACE_LINK_UNAVAILABLE")
    return result
