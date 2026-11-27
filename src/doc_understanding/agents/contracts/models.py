"""Source-anchored Agent contracts. Schema validity never establishes truth."""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = Annotated[str, Field(min_length=1)]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Mode = Literal["independent", "candidate_assisted"]
State = Literal["observed", "inferred", "unresolved"]


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode("utf-8")).hexdigest()


class Artifact(Draft):
    artifact_id: ID
    kind: Literal["pdf", "document", "physical", "structured", "overlay", "sidecar", "review"]
    uri: ID
    sha256: Hash
    document_sha256: Hash
    schema_version: str | None = None
    run_id: str | None = None
    json_pointer: str = ""


class EvidenceItem(Draft):
    evidence_id: ID
    artifact_id: ID
    source_id: ID
    kind: Literal["cell", "span", "page_region", "boundary"]
    page: int = Field(gt=0)
    bbox: tuple[float, float, float, float]
    text_raw: str | None = None
    physical_state: State = "unresolved"
    row_index: int | None = Field(default=None, ge=0)
    column_index: int | None = Field(default=None, ge=0)
    rowspan: int | None = Field(default=None, gt=0)
    colspan: int | None = Field(default=None, gt=0)
    occupied_slots: tuple[tuple[int, int], ...] = ()
    boundary_source_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def geometry(self):
        x0, y0, x1, y1 = self.bbox
        if x0 > x1 or y0 > y1:
            raise ValueError("unordered bbox")
        if self.kind != "cell" and self.occupied_slots:
            raise ValueError("only a cell has occupancy")
        return self


class EvidenceBundle(Draft):
    schema_version: Literal["agent-evidence/0.1-draft"] = "agent-evidence/0.1-draft"
    bundle_id: ID
    mode: Mode
    artifacts: tuple[Artifact, ...]
    items: tuple[EvidenceItem, ...]
    candidates: tuple[Artifact, ...] = ()
    bbox_space: Literal["pdf_points_top_left"] = "pdf_points_top_left"
    available_context: tuple[str, ...] = ()
    missing_context: tuple[str, ...] = ()
    excluded_source_ids: tuple[str, ...] = ()
    content_is_untrusted: Literal[True] = True

    @model_validator(mode="after")
    def source_channel(self):
        if any(a.kind in ("structured", "overlay", "sidecar") for a in self.artifacts):
            raise ValueError("Rule interpretation belongs in optional candidate channel")
        if self.mode == "independent" and self.candidates:
            raise ValueError("independent input cannot contain Rule candidates")
        if self.mode == "candidate_assisted" and not self.candidates:
            raise ValueError("assisted mode must identify its candidate snapshot")
        if any(a.kind not in ("structured", "overlay", "sidecar") for a in self.candidates):
            raise ValueError("invalid candidate artifact")
        aids = [a.artifact_id for a in self.artifacts + self.candidates]
        if len(aids) != len(set(aids)):
            raise ValueError("duplicate artifact ID")
        eids = [e.evidence_id for e in self.items]
        if len(eids) != len(set(eids)):
            raise ValueError("duplicate evidence ID")
        source_ids = {a.artifact_id for a in self.artifacts}
        if any(e.artifact_id not in source_ids for e in self.items):
            raise ValueError("evidence must anchor to source channel")
        return self


class Anchor(Draft):
    evidence_id: ID
    # Unicode code-point half-open interval in exact evidence text, not PDF bytes.
    text_range: tuple[int, int] | None = None


class LogicalNode(Draft):
    node_id: ID
    kind: Literal[
        "document",
        "table",
        "panel",
        "row",
        "column",
        "group",
        "record",
        "item",
        "context",
        "formula",
    ]
    anchors: tuple[Anchor, ...] = Field(min_length=1)
    axis: Literal["row", "column", "none"] = "none"
    panel_id: str | None = None
    label_proposed: str | None = None
    label_origin: Literal["source", "inferred"] = "source"
    status: Literal["proposed", "review_required"] = "proposed"


class Qualifier(Draft):
    role: Literal[
        "period",
        "unit",
        "scale",
        "measure",
        "scenario",
        "direction",
        "condition",
        "record_attribute",
    ]
    text: ID
    evidence_ids: tuple[ID, ...] = Field(min_length=1)
    state: State = "inferred"


class Scope(Draft):
    included: tuple[ID, ...] = ()
    excluded: tuple[ID, ...] = ()
    qualifiers: tuple[Qualifier, ...] = ()
    completeness: Literal["unknown", "claimed_complete", "partial"] = "unknown"


class Operand(Draft):
    ref: ID
    role: ID
    coefficient: Literal[-1, 1] = 1


class NumericPolicy(Draft):
    blank: Literal["unresolved", "zero_explicit"] = "unresolved"
    dash: Literal["unresolved", "zero_explicit"] = "unresolved"
    zero_denominator: Literal["unresolved"] = "unresolved"
    tolerance: str | None = None
    rounding: str | None = None
    authorization_ref: str | None = None

    @model_validator(mode="after")
    def explicit_zero(self):
        if "zero_explicit" in (self.blank, self.dash) and not self.authorization_ref:
            raise ValueError("zero policy requires explicit authorization provenance")
        return self


class Operation(Draft):
    operation_id: ID
    operator: Literal["sum", "subtract", "multiply", "divide"]
    operands: tuple[Operand, ...] = Field(min_length=1)
    result_ref: str | None = None
    formula_evidence_ids: tuple[ID, ...] = ()
    value_state: Literal["symbolic", "source_bound"] = "symbolic"
    policy: NumericPolicy = Field(default_factory=NumericPolicy)

    @model_validator(mode="after")
    def arity(self):
        if self.operator in ("subtract", "divide") and len(self.operands) != 2:
            raise ValueError("binary operation requires two ordered operands")
        if self.operator == "multiply" and len(self.operands) < 2:
            raise ValueError("multiply requires at least two operands")
        if len({o.ref for o in self.operands}) != len(self.operands) and self.operator == "sum":
            raise ValueError("duplicate aggregation operand")
        return self


class RelationClaim(Draft):
    claim_id: ID
    type: Literal[
        "CHILD_OF", "AGGREGATES", "DERIVED_FROM", "REFERS_TO", "APPLIES_TO", "CONTINUES_AS"
    ]
    source: ID
    targets: tuple[ID, ...] = Field(min_length=1)
    layer: Literal["semantic", "physical_containment"] = "semantic"
    scope: Scope = Field(default_factory=Scope)
    operation_id: str | None = None
    evidence_ids: tuple[ID, ...] = Field(min_length=1)
    counterevidence_ids: tuple[ID, ...] = ()
    alternative_set_id: str | None = None
    conflicts_with: tuple[ID, ...] = ()
    status: Literal["proposed", "review_required"] = "proposed"
    rationale: str = ""
    uncertainty: tuple[str, ...] = ()

    @model_validator(mode="after")
    def relation_shape(self):
        if len(set(self.targets)) != len(self.targets):
            raise ValueError("duplicate relation target")
        if self.type in ("CHILD_OF", "CONTINUES_AS") and len(self.targets) != 1:
            raise ValueError("one target per hierarchy/continuation claim")
        if self.type in ("AGGREGATES", "DERIVED_FROM") and not self.operation_id:
            raise ValueError("calculation relation requires operation")
        if self.layer == "physical_containment" and self.type != "CHILD_OF":
            raise ValueError("physical containment is a separate child relation layer")
        return self


class TreeProjection(Draft):
    tree_id: ID
    axis: Literal["row", "column"]
    node_ids: tuple[ID, ...]
    edge_claim_ids: tuple[ID, ...]
    root_candidate_ids: tuple[ID, ...] = ()
    status: Literal["proposed", "review_required"] = "proposed"


class CellBinding(Draft):
    binding_id: ID
    cell_evidence_id: ID
    row_path: tuple[ID, ...] = ()
    column_path: tuple[ID, ...] = ()
    measure_role: str | None = None
    qualifiers: tuple[Qualifier, ...] = ()
    alternative_set_id: str | None = None
    status: Literal["proposed", "unresolved"] = "proposed"


class Ambiguity(Draft):
    ambiguity_id: ID
    targets: tuple[ID, ...]
    reason: ID
    candidate_claim_ids: tuple[ID, ...] = ()
    review_evidence_ids: tuple[ID, ...] = ()
    required_context: tuple[str, ...] = ()


class Coverage(Draft):
    evidence_id: ID
    disposition: Literal[
        "bound", "role_only", "unresolved", "excluded_with_reason", "non_table", "root_candidate"
    ]
    reason: ID


class StructureProposalBundle(Draft):
    schema_version: Literal["agent-structure/0.1-draft"] = "agent-structure/0.1-draft"
    proposal_id: ID
    evidence_sha256: Hash
    mode: Mode
    origin: Literal["design_example", "synthetic_fixture", "agent_execution"]
    interpreter_id: str | None = None
    configuration_sha256: Hash | None = None
    nodes: tuple[LogicalNode, ...] = ()
    relations: tuple[RelationClaim, ...] = ()
    operations: tuple[Operation, ...] = ()
    trees: tuple[TreeProjection, ...] = ()
    bindings: tuple[CellBinding, ...] = ()
    ambiguities: tuple[Ambiguity, ...] = ()
    coverage: tuple[Coverage, ...] = ()

    @model_validator(mode="after")
    def unique_ids(self):
        groups = [
            (self.nodes, "node_id"),
            (self.relations, "claim_id"),
            (self.operations, "operation_id"),
            (self.trees, "tree_id"),
            (self.bindings, "binding_id"),
            (self.ambiguities, "ambiguity_id"),
        ]
        ids = [getattr(v, attr) for values, attr in groups for v in values]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate proposal ID")
        if self.origin == "agent_execution" and (
            not self.interpreter_id or not self.configuration_sha256
        ):
            raise ValueError("executed Agent must record interpreter and configuration")
        return self


class Check(Draft):
    dimension: Literal[
        "source",
        "structure",
        "binding",
        "operation",
        "scope",
        "conflict",
        "coverage",
        "semantic",
        "human_review",
    ]
    code: ID
    outcome: Literal["pass", "fail", "not_evaluated", "not_applicable"]
    target_ids: tuple[ID, ...] = ()
    message: str = ""


class ValidationResult(Draft):
    schema_version: Literal["agent-validation/0.1-draft"] = "agent-validation/0.1-draft"
    evidence_sha256: Hash
    proposal_sha256: Hash
    validator_version: Literal["agent-contract-checks/0.1"] = "agent-contract-checks/0.1"
    checks: tuple[Check, ...]
    semantic_truth: Literal["not_established"] = "not_established"
    fact_publication_allowed: Literal[False] = False

    @property
    def admissible(self):
        return not any(c.outcome == "fail" for c in self.checks)


class CanonicalSnapshot(Draft):
    schema_version: Literal["agent-canonical/0.1-draft"] = "agent-canonical/0.1-draft"
    evidence: EvidenceBundle
    proposal: StructureProposalBundle
    validation: ValidationResult
    fact_publication_allowed: Literal[False] = False

    @model_validator(mode="after")
    def hashes(self):
        if (
            self.proposal.evidence_sha256 != self.evidence.digest()
            or self.validation.evidence_sha256 != self.evidence.digest()
            or self.validation.proposal_sha256 != self.proposal.digest()
        ):
            raise ValueError("stale canonical inputs")
        return self


class GroundedText(Draft):
    text: str
    evidence_ids: tuple[ID, ...] = Field(min_length=1)
    claim_ids: tuple[ID, ...] = ()
    status: Literal["provisional", "source_transcription"] = "provisional"


class RetrievalUnit(Draft):
    unit_id: ID
    level: Literal["table", "group", "document"]
    summary: GroundedText
    schema_narration: GroundedText
    content_signature: tuple[GroundedText, ...] = ()
    node_paths: tuple[tuple[ID, ...], ...] = ()
    binding_ids: tuple[ID, ...] = ()
    relation_ids: tuple[ID, ...] = ()
    source_artifact_ids: tuple[ID, ...] = ()


class RAGProjection(Draft):
    schema_version: Literal["agent-rag-projection/0.1-draft"] = "agent-rag-projection/0.1-draft"
    projection_id: ID
    canonical_sha256: Hash
    policy: Literal["exploratory_provisional", "source_only"] = "source_only"
    units: tuple[RetrievalUnit, ...]
    answer_ready: Literal[False] = False
    # Value cells are dereferenced from canonical -> source, never copied here.


class InterpretationComparison(Draft):
    schema_version: Literal["agent-comparison/0.1-draft"] = "agent-comparison/0.1-draft"
    source_evidence_digest: Hash
    independent_bundle_sha256: Hash
    assisted_bundle_sha256: Hash
    candidate_artifact_hashes: tuple[Hash, ...] = Field(min_length=1)
    gold_artifact_sha256: Hash | None = None
    gold_status: Literal["provisional", "independent_reviewed", "missing"]
    model_id: str | None = None
    max_input_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    metrics: tuple[str, ...] = (
        "parent_precision_recall",
        "exact_path",
        "column_path",
        "cell_binding",
        "aggregation_scope",
        "operation",
        "continuation",
        "evidence",
        "abstention",
        "coverage",
        "new_correct_relations",
        "baseline_error_retention",
    )
    execution_status: Literal["not_executed"] = "not_executed"
