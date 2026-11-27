"""teams / table_review / state."""

from typing import TypedDict


class State(TypedDict, total=False):
    candidates: list
    routes: list
    needs_model: bool
    batch_results: list
    routing_summary: dict
    outcomes: list
    review_complete: bool
    roles: dict
    relations: dict
    judgment: dict
    interpretation: dict
    result: dict
