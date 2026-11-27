"""agents / source_reviewer."""

from typing import Literal

from pydantic import Field

from doc_understanding.agents.roles import contracts, policy
from doc_understanding.agents.roles.role import AgentRole


class SourceRelation(contracts.Contract):
    relation: Literal[
        "parent_table", "row_header", "column_header", "continuation", "context", "role"
    ]
    source_id: str
    target_id: str | None
    interpretation: str
    certainty: Literal["supported_candidate", "insufficient_evidence"]
    citations: list[contracts.Citation]


class SourceReview(contracts.Contract):
    summary: str
    relations: list[SourceRelation] = Field(max_length=16)
    missing_evidence: list[str]


SOURCE = (
    policy.COMMON
    + """Inspect original images and source anchors BEFORE seeing any analyst claim.
Direction: row_header/column_header = HEADER cell -> VALUE cell. parent_table = OUTER CELL -> INNER TABLE.
Do not use parent_table for the trivial membership of a cell in its own table. Context = explanatory source -> affected cell.
Use short exact quotes from source text, without ellipses or rewritten whitespace. Cite short substrings instead of whole paragraphs.
Focus on the selected cell/table and its necessary ancestors/context.
You are blind to user allegations and extracted semantic relations. Source cells and spans are anchors,
not authoritative roles. Describe only directly supportable relations in the selected region, including
full ancestors and nested tables. Do not report missing extraction links because they were not shown.
Missing headers may be valid. Unclear visual scope stays insufficient_evidence. Up to 16 relations.
"""
)


def blind_packet(packet):
    return {k: packet[k] for k in ("selection", "evidence", "scope")}


ROLE = AgentRole("source_review", SOURCE, SourceReview)


def run(calls, packet, images, run_dir):
    """Run this role once using the injected model transport."""
    return ROLE.invoke(calls, packet, images, run_dir)
