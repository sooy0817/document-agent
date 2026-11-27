"""teams / legacy_review / graph."""

from pathlib import Path

from doc_understanding.agents.roles import analyst as analyst_agent
from doc_understanding.agents.roles import judge as judge_agent
from doc_understanding.runtime.telemetry import Trace
from doc_understanding.runtime import artifacts, model_client
from doc_understanding.agents.workflows.legacy_review import state as legacy_review_state
from doc_understanding.agents.workflows.legacy_review import version
from doc_understanding.tools import evidence
from doc_understanding.validation import review, source_integrity


def run_review(record, data, progress, rid, *, calls, trace=None):
    from langgraph.graph import END, START, StateGraph

    trace = trace or Trace()
    run_dir = Path(data) / "review-queue" / "agents" / rid
    run_dir.mkdir(parents=True, exist_ok=True)
    context = {}
    records = []

    def collect(state):
        progress("collect_evidence")
        with trace.span(
            "collect_evidence",
            dict(run_id=rid, source_refs=[record["snapshot"]["source_pdf_sha256"]]),
        ) as box:
            local = source_integrity.inspect(record, data, lambda _: None)
            if any(f["code"] in ("SOURCE_CHANGED", "SOURCE_MISSING") for f in local["findings"]):
                context["blocked"] = "SOURCE_CHANGED_OR_MISSING"
            else:
                try:
                    packet, images = evidence.collect_evidence(record, data)
                    frozen = run_dir / "evidence.json"
                    if frozen.exists() and artifacts.digest(
                        artifacts.read(frozen)
                    ) != artifacts.digest(packet):
                        raise ValueError("PINNED_EVIDENCE_CHANGED")
                    if not frozen.exists():
                        artifacts.save(frozen, packet)
                    context.update(packet=packet, images=images)
                    for index, i in enumerate(images):
                        path = run_dir / f"source-{index}.png"
                        if not path.exists():
                            path.write_bytes(i["bytes"])
                except Exception as exc:
                    context["blocked"] = (
                        str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                    )
            box["output"] = dict(
                status="blocked" if "blocked" in context else "ready",
                reason=context.get("blocked"),
                source_refs=[e["id"] for e in context.get("packet", {}).get("evidence", [])][:20],
            )
        return dict(local=local)

    def invoke(role, packet):
        progress("analyze" if role == "analyst" else "judge")
        with trace.span(
            "analyze" if role == "analyst" else "independent_judge",
            dict(run_id=rid, mode=role),
            generation=True,
        ) as box:
            result = {"analyst": analyst_agent.run, "judge": judge_agent.run}[role](
                calls, packet, context["images"], run_dir
            )
            records.append(result)
            output = result.get("output") or {}
            box["output"] = dict(
                model=model_client.MODEL,
                status=result["status"],
                usage=result.get("usage") or {},
                estimated_cost_usd=result.get("estimated_cost_usd") or 0,
                reason=output.get("summary", ""),
                errors=[result.get("error")] if result.get("error") else [],
                candidates=[
                    dict(
                        kind=i.get("stage"),
                        reason=i.get("observation", i.get("reason")),
                        source_refs=[c["source_id"] for c in i["citations"]],
                    )
                    for i in output.get("issues", output.get("assessments", []))
                ],
            )
        return result

    def analyze(state):
        if "blocked" in context:
            return {}
        return dict(analysis=invoke("analyst", context["packet"]))

    def judge(state):
        if not state.get("analysis", {}).get("output"):
            return {}
        packet = {**context["packet"], "analyst_candidate": state["analysis"]["output"]}
        return dict(judge=invoke("judge", packet))

    def validate(state):
        progress("validate_review")
        with trace.span("validate_review", dict(run_id=rid)) as box:
            errors = review.check_outputs(
                context.get("packet", {"evidence": []}),
                state.get("analysis", {}).get("output"),
                state.get("judge", {}).get("output"),
            )
            if "blocked" in context:
                errors.append(context["blocked"])
            box["output"] = dict(errors=errors, semantic_status="human_review_pending")
        return dict(validation_errors=errors)

    def report(state):
        progress("report_ready")
        analysis = state.get("analysis", {}).get("output")
        judge = state.get("judge", {}).get("output")
        unresolved = bool(
            state["validation_errors"] or context.get("packet", {}).get("omitted_ids")
        )
        if analysis and judge:
            unresolved |= bool(
                analysis["missing_evidence"]
                or judge["missing_evidence"]
                or judge["additional_concerns"]
                or any(a["verdict"] != "agree" for a in judge["assessments"])
                or any(i["certainty"] == "insufficient_evidence" for i in analysis["issues"])
            )
        costs = [r["estimated_cost_usd"] for r in records]
        output = {
            **state["local"],
            "checker_version": version.VERSION,
            "mode": "agents",
            "verdict": "insufficient_evidence"
            if unresolved
            else "suspected_issue"
            if analysis and analysis["issues"]
            else "no_structural_issue_detected",
            "analysis": analysis,
            "judge": judge,
            "validation_errors": state["validation_errors"],
            "api_calls": sum(r["api_calls"] for r in records),
            "estimated_cost_usd": None if any(c is None for c in costs) else sum(costs),
            "call_records": [
                {k: v for k, v in r.items() if k not in ("response_text", "output")}
                for r in records
            ],
            "evidence_scope": dict(
                scope=context.get("packet", {}).get("scope"),
                omitted_ids=context.get("packet", {}).get("omitted_ids", []),
            ),
            "trace_id": trace.trace_id,
            "trace_warnings": trace.warnings,
            "limitations": [
                "모델 분석·검수는 후보입니다. 코드 검사는 의미의 정답을 보장하지 않습니다.",
                "분석과 검수는 같은 모델의 별도 호출이므로 공통 오판이 남을 수 있습니다.",
                "선택 영역과 제한된 문맥만 검토하며 파서·원본 JSON·정답 참조를 변경하지 않습니다.",
            ],
        }
        return dict(result=output)

    graph = StateGraph(legacy_review_state.State)
    for name, node in [
        ("collect_evidence", collect),
        ("analyze", analyze),
        ("judge", judge),
        ("validate_review", validate),
        ("report", report),
    ]:
        graph.add_node(name, node)
    graph.add_edge(START, "collect_evidence")
    graph.add_edge("collect_evidence", "analyze")
    graph.add_edge("analyze", "judge")
    graph.add_edge("judge", "validate_review")
    graph.add_edge("validate_review", "report")
    graph.add_edge("report", END)
    with trace.span("document-review-agents", dict(run_id=rid, mode=version.VERSION)) as root:
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
            semantic_status="human_review_pending",
        )
    trace.flush()
    if trace.client and trace.trace_id:
        try:
            result["trace_url"] = trace.client.get_trace_url(trace_id=trace.trace_id)
        except Exception:
            trace.warnings.append("LANGFUSE_TRACE_LINK_UNAVAILABLE")
    return result
