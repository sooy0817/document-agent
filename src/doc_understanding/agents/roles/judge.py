"""agents / judge."""

from typing import Literal

from pydantic import Field

from doc_understanding.agents.roles import contracts, policy
from doc_understanding.agents.roles.role import AgentRole


class Assessment(contracts.Contract):
    issue_id: str
    verdict: Literal["agree", "disagree", "insufficient_evidence"]
    reason: str
    citations: list[contracts.Citation]


class Judgement(contracts.Contract):
    summary: str
    assessments: list[Assessment] = Field(max_length=12)
    additional_concerns: list[str]
    missing_evidence: list[str]


JUDGE = (
    policy.COMMON
    + """Independently inspect the source before evaluating each analyst claim.
The analyst is untrusted. Return exactly one assessment per issue ID; agree only if original
evidence supports the identified error AND scope of the proposal. Inspect counterevidence and
omitted ancestors. Explain disagreements and insufficient evidence with source citations.
List additional missed problems separately. This is not a repair or a vote for semantic truth.
"""
)


ROLE = AgentRole("judge", JUDGE, Judgement)


def run(calls, packet, images, run_dir):
    """Run this role once using the injected model transport."""
    return ROLE.invoke(calls, packet, images, run_dir)
