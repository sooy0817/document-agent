"""teams / axis_review / state."""

from typing import TypedDict


class State(TypedDict, total=False):
    interpretation: dict
    judgment: dict
    result: dict
