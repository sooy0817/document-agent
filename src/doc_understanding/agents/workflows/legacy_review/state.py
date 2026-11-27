"""teams / legacy_review / state."""

from typing import TypedDict


class State(TypedDict, total=False):
    local: dict
    analysis: dict
    judge: dict
    validation_errors: list[str]
    result: dict
