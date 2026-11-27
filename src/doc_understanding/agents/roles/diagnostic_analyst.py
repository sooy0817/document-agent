"""agents / diagnostic_analyst."""

from typing import Literal

from pydantic import Field

from doc_understanding.agents.roles import analyst, contracts, policy
from doc_understanding.agents.roles.role import AgentRole


class Request(contracts.Contract):
    tool: Literal["read_source", "lookup_relation", "page_context"]
    source_id: str | None
    target_id: str | None
    relation: Literal["parent_table", "row_header", "column_header", "continuation"] | None
    page: int | None
    reason: str


class Issue(analyst.Issue):
    claim: Literal["missing", "incorrect", "uncertain"]


class StoredClaim(contracts.Contract):
    relation: Literal["parent_table", "row_header", "column_header", "continuation"]
    source_id: str
    target_id: str
    stored_status: Literal["present", "not_found_in_snapshot", "unknown"]


class Analysis(contracts.Contract):
    summary: str
    issues: list[Issue] = Field(max_length=12)
    missing_evidence: list[str]
    requests: list[Request] = Field(max_length=2)
    stored_claims: list[StoredClaim] = Field(max_length=16)


ANALYST = (
    policy.COMMON
    + """You diagnose a frozen extraction, not just the visible PDF.
Direction: parent_table = outer container cell -> inner TABLE (never cell -> its own table).
row_header/column_header = HEADER cell -> VALUE cell. continuation = earlier TABLE -> later TABLE.
Every assertion that an extracted relation is stored must appear in stored_claims with its exact IDs.
For a selected value cell, explicitly check relevant row/column/parent relations in stored_claims.
Do not say a relation is saved just because it is visually obvious. Empty row/column mappings are not saved paths.
Stay within the selected cell/table and its necessary ancestors/context; unrelated page defects are out of scope.
Inspect relation_inventory BEFORE alleging missing parent/axis/continuation relations.
A flattened parent text may coexist with separately linked nested tables; that is not itself failure.
Presence is not correctness. Separate missing vs incorrect vs uncertain claims explicitly.
For missing relation claims give precise source_id and target_id. Unknown IDs must not be invented.
If a relationship is present, do not propose adding it; inspect scope, paths or display instead.
Do not infer display defects without actual rendering evidence. No issue is a valid result.
Request up to 2 read-only tools only if needed: read_source(ID), lookup_relation(relation, source_id,
target_id), page_context(page). Page context only selected or adjacent pages. Non-applicable fields null.
Existing inventory and tool results are authoritative for storage presence, NOT semantic correctness.
Never quote generated table-wide text against a table ID with empty text. Cite exact cell substrings.
On reanalysis use supplied tool_results and initial claims; withdraw refuted claims. No second expansion.
"""
)


ROLE = AgentRole("analyst_v2", ANALYST, Analysis)


def run(calls, packet, images, run_dir):
    """Run this role once using the injected model transport."""
    return ROLE.invoke(calls, packet, images, run_dir)


REANALYSIS = AgentRole("reanalysis", ANALYST, Analysis)


def reanalyze(calls, packet, images, run_dir):
    """The single follow-up call uses its existing distinct replay key."""
    return REANALYSIS.invoke(calls, packet, images, run_dir)
