"""agents / role_classifier."""

from typing import Literal

from pydantic import Field

from doc_understanding.agents.roles import contracts, policy
from doc_understanding.agents.roles.role import AgentRole
from doc_understanding.tools import focused_evidence


class Role(contracts.Contract):
    cell_id: str
    role: Literal["value", "header", "description", "blank", "unknown"]
    reason: str
    citations: list[contracts.Citation]


class Roles(contracts.Contract):
    summary: str
    targets: list[Role] = Field(max_length=focused_evidence.MAX_TARGETS)
    missing_evidence: list[str]


ROLE_PROMPT = (
    policy.COMMON
    + """Describe the content role of every target exactly once, with exact citations.
value/header/description/blank/unknown describe the cell's contents, NOT whether another cell applies.
A long descriptive cell can still belong to a row item. Do not decide relationships or their absence.
Do not infer a role from its numeric/non-numeric nature alone. Unknown is valid. No storage claims.
"""
)


ROLE = AgentRole("classify_roles", ROLE_PROMPT, Roles)


def run(calls, packet, images, run_dir):
    """Run this role once using the injected model transport."""
    return ROLE.invoke(calls, packet, images, run_dir)
