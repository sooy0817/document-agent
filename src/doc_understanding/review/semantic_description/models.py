from typing import Literal
from pydantic import Field, model_validator
from doc_understanding.agents.contracts.models import Draft, Hash, ID


class Assertion(Draft):
    relation_type: Literal[
        "CHILD_OF", "AGGREGATES", "DERIVED_FROM", "REFERS_TO", "APPLIES_TO", "CONTINUES_AS"
    ]
    source: ID
    targets: tuple[ID, ...] = Field(min_length=1)
    claim_id: ID


class Description(Draft):
    description_id: ID
    level: Literal["table", "metric", "analysis"]
    subject_ids: tuple[ID, ...] = Field(min_length=1)
    text: str = Field(min_length=1)
    evidence_ids: tuple[ID, ...] = Field(min_length=1)
    assertions: tuple[Assertion, ...] = ()
    operation_ids: tuple[ID, ...] = ()
    epistemic_status: Literal["inferred", "unresolved", "hypothesis"]
    missing_context: tuple[str, ...] = ()
    interpretation_questions: tuple[str, ...] = ()
    reuse_context: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def hypothesis_is_not_fact(self):
        if self.level == "analysis" and self.epistemic_status != "hypothesis":
            raise ValueError("Analysis must remain a hypothesis, never a verified fact")
        return self


class SemanticDescriptionBundle(Draft):
    schema_version: Literal["semantic-description/0.1"] = "semantic-description/0.1"
    evidence_sha256: Hash
    proposal_sha256: Hash
    origin: Literal["interactive_agent_source_analysis", "external_model_execution", "mock"]
    interpreter: ID
    descriptions: tuple[Description, ...]
    fact_publication_allowed: Literal[False] = False

    @model_validator(mode="after")
    def unique(self):
        ids = [d.description_id for d in self.descriptions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate description ID")
        return self


def validate_descriptions(evidence, proposal, bundle):
    checks = []

    def check(code, ok, ids=()):
        checks.append(
            dict(code=code, status="pass" if ok else "conflict", description_ids=list(ids))
        )

    check("EVIDENCE_VERSION", bundle.evidence_sha256 == evidence.digest())
    check("PROPOSAL_VERSION", bundle.proposal_sha256 == proposal.digest())
    ev = {x.evidence_id for x in evidence.items}
    nodes = {x.node_id for x in proposal.nodes}
    claims = {x.claim_id: x for x in proposal.relations}
    ops = {x.operation_id for x in proposal.operations}
    for d in bundle.descriptions:
        check("EVIDENCE_REFERENCE", set(d.evidence_ids) <= ev, [d.description_id])
        check("SUBJECT_REFERENCE", set(d.subject_ids) <= nodes, [d.description_id])
        check("OPERATION_REFERENCE", set(d.operation_ids) <= ops, [d.description_id])
        for a in d.assertions:
            c = claims.get(a.claim_id)
            check(
                "TREE_DESCRIPTION_CONFLICT",
                bool(
                    c
                    and c.type == a.relation_type
                    and c.source == a.source
                    and set(c.targets) == set(a.targets)
                ),
                [d.description_id],
            )
    return dict(
        checks=checks,
        structurally_consistent=all(c["status"] == "pass" for c in checks),
        accuracy=dict(
            physical="not_evaluated_against_independent_gold",
            semantic_relations="unreviewed",
            semantic_prose="unreviewed",
            numeric="separate_operation_checks",
            evidence="references_checked_not_truth",
        ),
        prose_semantics_not_automatically_verified=True,
        fact_publication_allowed=False,
    )
