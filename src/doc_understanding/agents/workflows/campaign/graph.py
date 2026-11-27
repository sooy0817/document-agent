"""Bounded LangGraph coordinator; no model calls or implicit retries."""

from langgraph.graph import END, START, StateGraph

from .policy import next_action
from .state import CampaignState


def build_graph(store, parse_sample, review_sample, *, specialist=None, checkpointer=None):
    def supervisor(state):
        decision = next_action(store.samples(state["campaign_id"]))
        if specialist and decision["reason"] == "specialist_review_not_connected":
            if state.get("specialist_finished"):
                decision["reason"] = state["specialist_reason"]
            else:
                decision.update(next_team="specialist", reason="specialist_review_pending")
        store.event(state["campaign_id"], "supervisor", decision["reason"], decision["sample_id"])
        return decision

    def parse(state):
        parse_sample(state["campaign_id"], state["sample_id"])
        return {}

    def review(state):
        review_sample(state["campaign_id"], state["sample_id"])
        return {}

    def specialist_review(state):
        reason = specialist(state["campaign_id"])
        return {"specialist_finished": True, "specialist_reason": reason}

    builder = StateGraph(CampaignState)
    builder.add_node("supervisor", supervisor)
    builder.add_node("parse", parse)
    builder.add_node("review", review)
    builder.add_node("specialist", specialist_review)
    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges("supervisor", lambda s: s["next_team"], {"parse": "parse", "review": "review", "specialist": "specialist", "finish": END})
    builder.add_edge("parse", "supervisor")
    builder.add_edge("review", "supervisor")
    builder.add_edge("specialist", "supervisor")
    return builder.compile(checkpointer=checkpointer)
