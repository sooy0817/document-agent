# 역할: 질문 한 건의 실행 순서(노드와 분기)만 정한다. 각 단계가 하는 일은 run.py가 넘겨 준다.
"""질문 에이전트의 LangGraph 구성.

    plan_question → select_documents → gather_evidence → select_evidence ─┬─────────────────────→ generate_answer → (끝)
                          ↑                           └→ expand_evidence ─────────↗      │
                          │                                   ↑                          │
                          │                                   └── 보류했고 아직 넓히지 않았으면 ──┤
                          └──────────── 근거가 부족한 질문 구간이 있으면 한 번만 ──────────────────┘

문서 고르기(select_documents): 질문이 가리킨 대상을 문서 이름과 맞춘다. 한 문서(또는 한 무리)로 정해지면 그 안에서만
찾고, 후보가 몇 개로 갈리면 되묻고, 우리 문서에 없는 대상이면 없다고 답한다. 뒤의 두 경우는 검색·답변 없이 끝난다(after_documents).

원문 격자 읽기(read_grid): 답이 보류로 끝났고, 검색에 걸린 표 가운데 구조 해석이 표 전체를 보류한 표(쓸 수 있는 칸 0)가
있으면 그 표의 원문 격자를 모델에 읽혀 한 번 더 답한다(`rag/agentic/grid.py`). 재검색과 표 전체 넓히기를 다 한 뒤의 마지막
시도이고, 질문 한 건에 한 번이다. 비교하는 두 문서 모두에서 질문에 명시한 행이 보류 표에만 있으면
select_evidence에서 해당 표들을 read_grid로 직접 보낸다.

읽기 범위 분기(reading_scope): 계획이 표·목록 전체를 읽어야 하는 질문이라고 하면 답하기 전에 근거가 속한 표를
통째로 넣는다(expand_evidence). 그렇지 않으면 선별된 근거로 바로 답한다. 바로 답했는데 보류가 나오면, 질문 한 건에
한 번만 넓혀서 다시 답한다. 분기는 계획의 reading 값과 실행 상태만 보고, 질문 문구나 문서 종류는 보지 않는다.

노드는 상태(AgentState)의 일부를 돌려주고, 여기서는 연결과 분기만 맡는다. 새 경로는 이 파일에 노드와 분기를 더해 만든다.
"""
from typing import TypedDict

NODES = ("plan_question", "select_documents", "gather_evidence", "select_evidence", "expand_evidence", "generate_answer",
         "read_grid")


class AgentState(TypedDict, total=False):
    question: str
    plan: object          # agentic.plan.QuestionPlan
    plan_check: dict
    evidence: list        # 지금 시도의 근거(E1.. 번호)
    focused: list         # 넓히기 전의 근거(넓힌 근거가 요청 한도를 넘으면 이것으로 답한다)
    trace: dict           # 지금 시도의 검색·구조 일치·선별 기록
    attempts: list        # 시도별 기록(재검색하면 2개)
    retry_quotes: list    # 미충족 요구에서 만든 새 검색어(기록 호환을 위해 기존 이름 유지). 이것만 재조회한다.
    retry_base: list      # 재검색 전 답변과 연결된 원래 근거. 새 근거가 없으면 이것을 유지한다.
    result: dict          # 마지막 답변 결과
    answer_calls: list    # 답변 호출별 모델 호출 수
    expanded: bool        # 지금 근거가 표 전체로 넓힌 것인가
    widened: bool         # 보류 뒤 넓혀서 다시 답하기를 이미 했는가(질문 한 건에 한 번)
    widen: bool           # 방금 답이 보류였고, 넓혀서 다시 답해 볼 수 있는가
    skip_answer: bool     # 넓혀도 더해진 근거가 없어 다시 답하지 않는다
    documents: dict       # 문서 고르기 결과(agentic.documents.select). 디렉터리가 없으면 None
    stop: bool            # 되묻기·문서 없음으로 끝낸다(검색·답변을 하지 않는다)
    grid: list            # 원문 격자로 읽어 볼 표들 [(run_id, table_id)]. 보류 뒤 또는 비교에 필요한 행을 직접 읽을 때 채운다


def after_documents(state):
    """되묻거나 문서가 없다고 답할 때는 여기서 끝낸다."""
    return "end" if state.get("stop") else "gather_evidence"


def reading_scope(state):
    """비교에 필요한 원문 격자를 먼저 읽고, 그 외에는 계획과 표 근거에 따라 넓히거나 답한다."""
    if state.get('skip_answer'):
        return 'read_grid' if state.get('grid') else 'end'
    if state.get("trace", {}).get("required_grid_rows"):
        return "read_grid"
    whole = getattr(state.get("plan"), "reading", "focused") == "whole"
    return "expand_evidence" if whole and any(e.get("table_id") for e in state.get("evidence") or []) else "generate_answer"


def after_expand(state):
    """넓혀도 더해진 근거가 없으면 다시 답하지 않는다. 그때도 구조가 확정되지 않은 표가 남아 있으면 원문 격자를 읽힌다."""
    if not state.get("skip_answer"):
        return "generate_answer"
    return "read_grid" if state.get("grid") else "end"


def after_answer(state):
    """근거가 부족한 질문 구간이 있고 아직 한 번도 다시 찾지 않았으면 재검색한다. 그게 아니고 보류였으면
    질문 한 건에 한 번만 표 전체로 넓혀 다시 답한다. 그래도 보류이고 구조가 확정되지 않은 표가 검색에 걸렸으면 그 표의
    원문 격자를 한 번 읽힌다. 자동 반복은 없다(답변 호출은 많아야 세 번, 격자 읽기 한 번)."""
    if state.get("retry_quotes") and len(state.get("attempts") or []) < 2:
        return "gather_evidence"
    if state.get("widen"):
        return "expand_evidence"
    return "read_grid" if state.get("grid") else "end"


def build(steps):
    """steps: 노드 이름 → 함수(state) -> 바뀐 상태. NODES의 이름을 모두 가져야 한다."""
    from langgraph.graph import END, START, StateGraph

    missing = [name for name in NODES if name not in steps]
    if missing:
        raise ValueError("AGENT_GRAPH_STEPS_MISSING")
    graph = StateGraph(AgentState)
    for name in NODES:
        graph.add_node(name, steps[name])
    graph.add_edge(START, "plan_question")
    graph.add_edge("plan_question", "select_documents")
    graph.add_conditional_edges("select_documents", after_documents, {"gather_evidence": "gather_evidence", "end": END})
    graph.add_edge("gather_evidence", "select_evidence")
    graph.add_conditional_edges("select_evidence", reading_scope,
                                {"expand_evidence": "expand_evidence", "generate_answer": "generate_answer",
                                 "read_grid": "read_grid", "end": END})
    graph.add_conditional_edges("expand_evidence", after_expand,
                                {"generate_answer": "generate_answer", "read_grid": "read_grid", "end": END})
    graph.add_conditional_edges("generate_answer", after_answer,
                                {"gather_evidence": "gather_evidence", "expand_evidence": "expand_evidence",
                                 "read_grid": "read_grid", "end": END})
    graph.add_edge("read_grid", END)
    return graph.compile()
