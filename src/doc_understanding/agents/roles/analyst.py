"""agents / analyst."""

from typing import Literal

from pydantic import Field

from doc_understanding.agents.roles import contracts, policy
from doc_understanding.agents.roles.role import AgentRole


class Issue(contracts.Contract):
    issue_id: str
    stage: Literal["physical", "parent_link", "axis_role", "context", "display", "unknown"]
    target_ids: list[str]
    observation: str
    proposal: str
    relation: Literal[
        "row_header", "column_header", "parent_table", "continuation", "context", "role", "none"
    ]
    source_id: str | None
    target_id: str | None
    citations: list[contracts.Citation]
    certainty: Literal["supported_candidate", "insufficient_evidence"]


class Analysis(contracts.Contract):
    summary: str
    issues: list[Issue] = Field(max_length=12)
    missing_evidence: list[str]


ANALYST = (
    policy.COMMON
    + """Analyze the selected area and feedback. Locate the earliest supported failure:
physical extraction, parent link, row/column role, context assembly, or display. User feedback is a
claim, not Gold. Describe suggested corrections only; relation proposals must reference existing IDs.
Use stable issue IDs I1, I2, etc. A supported candidate requires target IDs and source citations.
If a missing table has no extracted ID, cite the selected region/page evidence and mark the missing
structure; never fabricate a cell. If no issue is supported, return an empty issue list and explain.
"""
)


ROLE = AgentRole("analyst", ANALYST, Analysis)


def run(calls, packet, images, run_dir):
    """Run this role once using the injected model transport."""
    return ROLE.invoke(calls, packet, images, run_dir)
