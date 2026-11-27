"""agents / relation_analyst."""

from pydantic import Field

from doc_understanding.agents.roles import axis_interpreter, contracts, policy
from doc_understanding.agents.roles.role import AgentRole
from doc_understanding.tools import focused_evidence


class TargetRelations(contracts.Contract):
    cell_id: str
    target_quote: str
    row_axis: axis_interpreter.Axis
    column_axis: axis_interpreter.Axis
    context_ids: list[str]


class Relations(contracts.Contract):
    summary: str
    targets: list[TargetRelations] = Field(max_length=focused_evidence.MAX_TARGETS)
    missing_evidence: list[str]


RELATION_PROMPT = (
    policy.COMMON
    + """Identify applicable item/header relationships for EVERY target cell,
regardless of whether its content is a number, prose, a heading, or contains an inner table.
You are intentionally NOT given role classification. Role is never a reason to skip relationships.
For each target separately ask: which row item/group governs this content, and which column attribute?
An outer item label may govern a long description or a cell containing an inner table. Preserve that
outer relationship without importing inner-table headers into the outer cell's column path.
Return full source header cell ID paths in outer-to-inner order. Never invent a generic label.
row_axis relates the item/group to its content; column_axis identifies the attribute across rows.
geometric_candidates are alternatives to inspect, NOT correct answers. Nearby numbers, other item
labels, captions, or prose may be unrelated. Check original text, alignment, boundaries and scope.
no_header needs source evidence after considering alternatives. Unknown is valid. A classification
such as description/header is NOT evidence of no_header. Detached fragment connections stay contextual
candidates unless their continuity is supported. Do not claim document-wide absence from a crop.
Give target_quote as an EXACT short substring of this target's original text (empty only for a blank).
Each header_ids entry needs its own exact source citation. The program will attach target_quote with
the known target ID, so do not rewrite or normalize it. No storage claims or source edits.
"""
)


ROLE = AgentRole("infer_relations", RELATION_PROMPT, Relations)


def run(calls, packet, images, run_dir):
    """Run this role once using the injected model transport."""
    return ROLE.invoke(calls, packet, images, run_dir)
