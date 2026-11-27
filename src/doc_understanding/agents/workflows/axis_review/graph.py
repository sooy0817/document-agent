"""teams / axis_review / graph."""

from pathlib import Path

from doc_understanding.agents.roles import axis_interpreter, axis_judge
from doc_understanding.runtime import telemetry
from doc_understanding.runtime import artifacts
from doc_understanding.agents.workflows.axis_review import state as axis_review_state
from doc_understanding.agents.workflows.axis_review import version
from doc_understanding.tools import focused_evidence
from doc_understanding.validation import axes


def run_review(record, data, run_dir, *, calls, trace=None):
    """Explicit injected adapter required. This module never loads keys or starts live calls itself."""
    from langgraph.graph import END, START, StateGraph

    trace = trace or telemetry.Trace()
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    packet, images, index, audit = focused_evidence.prepare(record, data)
    for name, value in [("focused-evidence.json", packet), ("scope-audit.json", audit)]:
        path = run_dir / name
        if path.exists() and artifacts.digest(artifacts.read(path)) != artifacts.digest(value):
            raise ValueError("PINNED_INPUT_CHANGED")
        if not path.exists():
            artifacts.save(path, value)
    for i, image in enumerate(images):
        path = run_dir / f"focused-{i}.png"
        if not path.exists():
            path.write_bytes(image["bytes"])
    records = []

    def call(role, payload):
        with trace.span(role, dict(mode=version.VERSION), generation=True) as span:
            r = {"interpret_axes": axis_interpreter.run, "judge_axes": axis_judge.run}[role](
                calls, payload, images, run_dir
            )
            records.append(r)
            span["output"] = dict(
                status=r["status"],
                usage=r.get("usage"),
                estimated_cost_usd=r.get("estimated_cost_usd"),
            )
            if not r.get("output"):
                raise ValueError("MODEL_OUTPUT_UNAVAILABLE")
            return r["output"]

    def interpret(state):
        return dict(interpretation=call("interpret_axes", packet))

    def judge(state):
        return dict(
            judgment=call(
                "judge_axes", {**packet, "source_interpretation": state["interpretation"]}
            )
        )

    def verify(state):
        with trace.span("storage_lookup_and_validation", dict(mode=version.VERSION)) as span:
            r = axes.validate(packet, state["interpretation"], state["judgment"], index)
            span["output"] = dict(status=r["verdict"], errors=r["errors"])
        return dict(result=r)

    graph = StateGraph(axis_review_state.State)
    for name, node in [
        ("interpret_axes", interpret),
        ("judge_axes", judge),
        ("storage_lookup_and_validation", verify),
    ]:
        graph.add_node(name, node)
    graph.add_edge(START, "interpret_axes")
    graph.add_edge("interpret_axes", "judge_axes")
    graph.add_edge("judge_axes", "storage_lookup_and_validation")
    graph.add_edge("storage_lookup_and_validation", END)
    with trace.span("focused-axis-review", dict(mode=version.VERSION)) as root:
        if trace.client:
            try:
                trace.trace_id = trace.client.get_current_trace_id()
            except Exception:
                pass
        state = graph.compile().invoke({})
        root["output"] = dict(status=state["result"]["verdict"], errors=state["result"]["errors"])
    trace.flush()
    result = dict(
        version=version.VERSION,
        **state,
        call_records=records,
        parser_modified=False,
        semantic_verified=False,
        api_calls=sum(r.get("api_calls", 0) for r in records),
        trace_id=trace.trace_id,
        estimated_cost_usd=None
        if any(r.get("estimated_cost_usd") is None for r in records)
        else sum(r["estimated_cost_usd"] for r in records),
    )
    if trace.client and trace.trace_id:
        try:
            result["trace_url"] = trace.client.get_trace_url(trace_id=trace.trace_id)
        except Exception:
            pass
    path = run_dir / "result.json"
    if not path.exists():
        artifacts.save(path, result)
    return result
