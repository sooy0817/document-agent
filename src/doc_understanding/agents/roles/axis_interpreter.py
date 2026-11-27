"""agents / axis_interpreter."""

from typing import Literal

from pydantic import Field

from doc_understanding.agents.roles import contracts, policy
from doc_understanding.agents.roles.role import AgentRole
from doc_understanding.tools import focused_evidence


class Axis(contracts.Contract):
    status: Literal["explicit", "contextual_candidate", "no_header", "unknown"]
    header_ids: list[str] = Field(max_length=8)
    reason: str
    citations: list[contracts.Citation]


class Target(contracts.Contract):
    cell_id: str
    role: Literal["value", "header", "description", "blank", "unknown"]
    row_axis: Axis
    column_axis: Axis
    context_ids: list[str]


class Interpretation(contracts.Contract):
    summary: str
    targets: list[Target] = Field(max_length=focused_evidence.MAX_TARGETS)
    missing_evidence: list[str]


SOURCE_PROMPT = (
    policy.COMMON
    + """Interpret source meaning only. You are NOT asked whether JSON stores a path.
Return every target_id exactly once, including blanks/headers/unknown roles. Do not review other cells.
For each VALUE, identify row labels and column labels separately. A row label identifies an item/group
across that row; a column label identifies the attribute measured down a column. Left/above are clues,
not automatic role assignments. A neighbouring paragraph is not a header just because it is nearby.
Give complete ancestor HEADER cell IDs in outer-to-inner order. Do not place the VALUE in its own path.
Detached header fragments and continued tables are possible; use contextual_candidate if their
connection depends on unverified continuity. Do not invent a generic header such as 'content'.
Use no_header with a reason and source citations when absence is supported, unknown when context is
insufficient. A header/description/blank target is not a missing value mapping: its axes may be no_header.
Cite short exact source substrings from each proposed header AND the value. Do not claim other
conditions/exceptions are absent merely because this scoped packet does not show them.
context_ids are explanatory candidates, not established applicability. No storage claims, no repair.
"""
)


ROLE = AgentRole("interpret_axes", SOURCE_PROMPT, Interpretation)


def run(calls, packet, images, run_dir):
    """Run this role once using the injected model transport."""
    return ROLE.invoke(calls, packet, images, run_dir)
