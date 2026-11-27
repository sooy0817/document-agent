"""agents / relation_judge."""

from typing import Literal

from pydantic import Field

from doc_understanding.agents.roles import axis_judge, contracts, policy
from doc_understanding.agents.roles.role import AgentRole


class Coverage(contracts.Contract):
    cell_id: str
    axis: Literal["row", "column"]
    header_id: str
    requirement: Literal["required", "not_required", "insufficient_evidence"]
    reason: str
    citations: list[contracts.Citation]


class Judgment(axis_judge.Judgment):
    coverage: list[Coverage] = Field(max_length=160)


JUDGE_PROMPT = (
    policy.COMMON
    + """Independently audit each target's row and column relationship against source.
Return one decision for every target/axis and one coverage assessment for EVERY geometric_candidates
entry. These geometric neighbours are candidates, not Gold. Consider additional ancestors if present.
Determine required/not_required/insufficient_evidence using original labels, prose, cell boundaries,
merged scope and reading order. A prose/description cell can be governed by the item to its left.
A header role can also have an ancestor. Never accept an empty path only because content is descriptive.
If a required governing label is omitted, mark the axis contradicted and coverage required. Explain
the missing label with its source ID. Reject unrelated neighbours rather than forcing every candidate
into a path. Absence is allowed when supported; ambiguity stays insufficient_evidence.
Check inner versus outer table scope. Classifications do not suppress relation checks. Do not infer
correctness from storage presence or agreement with another model. Quote exact source substrings.
For each required relationship cite BOTH the candidate label and the target's text. No auto approval.
"""
)


ROLE = AgentRole("judge_relations", JUDGE_PROMPT, Judgment)


def run(calls, packet, images, run_dir):
    """Run this role once using the injected model transport."""
    return ROLE.invoke(calls, packet, images, run_dir)
