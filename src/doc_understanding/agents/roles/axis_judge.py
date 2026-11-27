"""agents / axis_judge."""

from typing import Literal

from pydantic import Field

from doc_understanding.agents.roles import contracts, policy
from doc_understanding.agents.roles.role import AgentRole
from doc_understanding.tools import focused_evidence


class Decision(contracts.Contract):
    cell_id: str
    axis: Literal["row", "column"]
    verdict: Literal["supported", "contradicted", "insufficient_evidence", "not_applicable"]
    reason: str
    citations: list[contracts.Citation]


class Judgment(contracts.Contract):
    summary: str
    decisions: list[Decision] = Field(max_length=2 * focused_evidence.MAX_TARGETS)
    missing_evidence: list[str]


JUDGE_PROMPT = (
    policy.COMMON
    + """Evaluate source meaning, not database presence. Return exactly one row and
one column decision for EACH target_id. Inspect images, text and candidate paths, including ancestor
omissions, narrative mislabelled as header, merged scope, detached fragments and row/column confusion.
Agree only where source supports the whole path; matching strings or coordinates alone are insufficient.
no_header is allowed; unknown requires insufficient_evidence. non-value roles may be not_applicable.
Use exact short citations; cannot establish document-wide absence from this selected scope.
Do not add targets or infer semantic truth from whether a path is stored. No auto-approval or rewriting.
"""
)


ROLE = AgentRole("judge_axes", JUDGE_PROMPT, Judgment)


def run(calls, packet, images, run_dir):
    """Run this role once using the injected model transport."""
    return ROLE.invoke(calls, packet, images, run_dir)
