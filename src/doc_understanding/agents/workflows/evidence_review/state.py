"""teams / evidence_review / state."""

from typing import TypedDict


class State(TypedDict, total=False):
    source: dict
    initial: dict
    analysis: dict
    checks: list
    tools: list
    judge: dict
    errors: list
    result: dict
