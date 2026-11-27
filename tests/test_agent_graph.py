"""질문 에이전트 그래프: 노드 순서, 읽기 범위 분기, 재검색·넓히기는 한 번만, 빠진 노드는 거부."""
from types import SimpleNamespace

import pytest

from doc_understanding.rag.agentic.graph import NODES, after_answer, after_documents, after_expand, build, reading_scope

TABLE = [dict(evidence_id="E1", table_id="T", cell_id="c1")]
BODY = [dict(evidence_id="E1", content_id="b1")]


def steps(order, answers, reading="focused", evidence=TABLE, adds=True, stop=False, unread=False):
    """노드가 실행된 순서를 기록하는 가짜 단계.
    answers: 답변 호출마다 (근거 부족 구간 목록, 보류 뒤 넓힐 수 있는가)."""
    def plan_question(state):
        order.append("plan_question")
        return dict(plan=SimpleNamespace(reading=reading), plan_check={})

    def select_documents(state):
        order.append("select_documents")
        return dict(stop=True, result=dict(call=0)) if stop else dict(documents=None)

    def gather_evidence(state):
        order.append(("gather_evidence", tuple(state.get("retry_quotes") or ())))
        return dict(evidence=list(evidence), trace={})

    def select_evidence(state):
        order.append("select_evidence")
        return dict(attempts=[*(state.get("attempts") or []), dict(trace=state["trace"])], expanded=False, widen=False,
                    skip_answer=False)

    def expand_evidence(state):
        order.append("expand_evidence")
        after_answer_call = bool(state.get("widen"))
        return dict(expanded=True, widen=False, widened=state.get("widened") or after_answer_call,
                    skip_answer=after_answer_call and not adds)

    def generate_answer(state):
        order.append("generate_answer")
        calls = [*(state.get("answer_calls") or []), 1]
        quotes, abstained = answers[len(calls) - 1]
        widen = abstained and not state.get("expanded") and not state.get("widened")
        return dict(result=dict(call=len(calls)), answer_calls=calls, retry_quotes=quotes, widen=widen,
                    grid=[("run", "table")] if abstained and unread else [])

    def read_grid(state):
        order.append("read_grid")
        return dict(result=dict(call="grid"), grid=[])

    return dict(plan_question=plan_question, select_documents=select_documents,
                gather_evidence=gather_evidence, select_evidence=select_evidence,
                expand_evidence=expand_evidence, generate_answer=generate_answer, read_grid=read_grid)


def run(order, *args, **kwargs):
    return build(steps(order, *args, **kwargs)).invoke(dict(question="q"))


def test_focused_question_answers_from_the_selected_evidence():
    order = []
    state = run(order, [([], False)])
    assert order == ["plan_question", "select_documents", ("gather_evidence", ()), "select_evidence", "generate_answer"]
    assert state["result"] == dict(call=1) and len(state["attempts"]) == 1


def test_whole_reading_expands_before_the_first_answer():
    order = []
    run(order, [([], False)], reading="whole")
    assert order == ["plan_question", "select_documents", ("gather_evidence", ()), "select_evidence", "expand_evidence", "generate_answer"]


def test_whole_reading_without_table_evidence_has_nothing_to_expand():
    order = []
    run(order, [([], False)], reading="whole", evidence=BODY)
    assert "expand_evidence" not in order


@pytest.mark.parametrize("reading", ["focused", "whole"])
@pytest.mark.parametrize("answered", [True, False])
def test_required_comparison_grid_runs_once_before_answering_or_expanding(reading, answered):
    order = []
    nodes = steps(order, [], reading=reading)
    select = nodes["select_evidence"]
    targets = [("left", "left-table"), ("right", "right-table")]
    withheld = dict(abstained=True, reasons=["REQUIRED_GRID_NOT_READ"])

    def select_evidence(state):
        return dict(select(state), trace=dict(required_grid_rows={table: "requested row" for _, table in targets}),
                    grid=targets, evidence=[], result=withheld)

    def read_grid(state):
        order.append("read_grid")
        assert state["grid"] == targets
        assert state["result"] == withheld
        return dict(grid=[], **(dict(result=dict(call="grid", abstained=False)) if answered else {}))

    nodes.update(select_evidence=select_evidence, read_grid=read_grid)
    state = build(nodes).invoke(dict(question="compare"))
    assert order == ["plan_question", "select_documents", ("gather_evidence", ()), "select_evidence", "read_grid"]
    assert state["result"] == (dict(call="grid", abstained=False) if answered else withheld)
    assert len(state["attempts"]) == 1 and not state.get("answer_calls")


def test_abstention_widens_once_and_answers_again():
    order = []
    # 넓혀서 다시 답해도 보류면 거기서 끝난다(또 넓히지 않는다).
    state = run(order, [([], True), ([], True)])
    assert order == ["plan_question", "select_documents", ("gather_evidence", ()), "select_evidence", "generate_answer",
                     "expand_evidence", "generate_answer"]
    assert state["result"] == dict(call=2)


def test_widening_that_adds_nothing_does_not_call_the_answer_model_again():
    order = []
    state = run(order, [([], True)], adds=False)
    assert order[-2:] == ["generate_answer", "expand_evidence"] and state["result"] == dict(call=1)


def test_unsupported_part_searches_again_once_and_never_loops():
    order = []
    # 재검색한 뒤에도 근거 부족·보류면 한 번 넓혀 답하고 끝난다. 답변 호출은 세 번을 넘지 않는다.
    state = run(order, [(["조건 A"], True), (["조건 A"], True), (["조건 A"], True), (["조건 A"], True)])
    assert order == ["plan_question", "select_documents", ("gather_evidence", ()), "select_evidence", "generate_answer",
                     ("gather_evidence", ("조건 A",)), "select_evidence", "generate_answer",
                     "expand_evidence", "generate_answer"]
    assert state["result"] == dict(call=3) and len(state["attempts"]) == 2


def test_an_abstention_with_an_unreadable_table_reads_its_raw_grid_last_and_once():
    order = []
    # 넓혀서 다시 답해도 보류면, 구조가 확정되지 않은 표를 원문 격자로 한 번 읽고 끝난다.
    state = run(order, [([], True), ([], True)], unread=True)
    assert order[-4:] == ["generate_answer", "expand_evidence", "generate_answer", "read_grid"]
    assert state["result"] == dict(call="grid")
    answered = []
    run(answered, [([], False)], unread=True)
    assert "read_grid" not in answered      # 답이 나왔으면 읽지 않는다
    # 넓혀도 더해진 근거가 없어 다시 답하지 않는 경우에도 격자 읽기는 한다.
    noop = []
    state = run(noop, [([], True)], unread=True, adds=False)
    assert noop[-3:] == ["generate_answer", "expand_evidence", "read_grid"] and state["result"] == dict(call="grid")


def test_asking_back_or_a_missing_document_ends_before_any_search():
    order = []
    state = run(order, [([], False)], stop=True)
    assert order == ["plan_question", "select_documents"] and state["result"] == dict(call=0)


def test_routes():
    assert reading_scope(dict(skip_answer=True))=='end'
    assert reading_scope(dict(skip_answer=True,grid=[('r','t')]))=='read_grid'
    assert after_documents(dict(stop=True)) == "end" and after_documents(dict()) == "gather_evidence"
    whole, focused = SimpleNamespace(reading="whole"), SimpleNamespace(reading="focused")
    assert reading_scope(dict(plan=whole, evidence=TABLE)) == "expand_evidence"
    assert reading_scope(dict(plan=whole, evidence=BODY)) == "generate_answer"
    assert reading_scope(dict(plan=focused, evidence=TABLE)) == "generate_answer"
    assert reading_scope(dict(plan=whole, evidence=TABLE, trace=dict(required_grid_rows={}))) == "expand_evidence"
    assert reading_scope(dict(plan=whole, evidence=TABLE, trace=dict(required_grid_rows={"T": "row"}))) == "read_grid"
    assert after_expand(dict(skip_answer=True)) == "end" and after_expand(dict()) == "generate_answer"
    assert after_expand(dict(skip_answer=True, grid=[("r", "t")])) == "read_grid"
    assert after_answer(dict(retry_quotes=["x"], attempts=[{}], widen=True)) == "gather_evidence"  # 재검색이 먼저
    assert after_answer(dict(retry_quotes=["x"], attempts=[{}, {}], widen=True)) == "expand_evidence"
    assert after_answer(dict(retry_quotes=["x"], attempts=[{}, {}])) == "end"
    assert after_answer(dict(retry_quotes=[], attempts=[{}])) == "end"
    assert after_answer(dict(retry_quotes=[], attempts=[{}], grid=[("r", "t")])) == "read_grid"
    assert after_answer(dict(retry_quotes=[], attempts=[{}], grid=[("r", "t")], widen=True)) == "expand_evidence"  # 넓히기가 먼저


def test_graph_needs_every_node():
    with pytest.raises(ValueError, match="AGENT_GRAPH_STEPS_MISSING"):
        build({name: (lambda state: {}) for name in NODES[:-1]})


def test_graph_shape_is_the_documented_one():
    drawn = build({name: (lambda state: {}) for name in NODES}).get_graph()
    edges = {(e.source, e.target) for e in drawn.edges}
    assert edges == {("__start__", "plan_question"), ("plan_question", "select_documents"),
                     ("select_documents", "gather_evidence"), ("select_documents", "__end__"),
                     ("gather_evidence", "select_evidence"),
                     ("select_evidence", "generate_answer"), ("select_evidence", "expand_evidence"),
                     ("select_evidence", "read_grid"), ("select_evidence", "__end__"),
                     ("expand_evidence", "generate_answer"), ("expand_evidence", "__end__"), ("expand_evidence", "read_grid"),
                     ("generate_answer", "gather_evidence"), ("generate_answer", "expand_evidence"),
                     ("generate_answer", "__end__"), ("generate_answer", "read_grid"), ("read_grid", "__end__")}
