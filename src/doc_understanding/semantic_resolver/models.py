from typing import Literal
from uuid import UUID
from pydantic import Field
from doc_understanding.domain.models import EvidenceModel, SourceRef, BBox

RelationType = Literal[
    "parent_child", "alternative_label", "annotation", "continuation", "related_adjustment"
]
Status = Literal["proposed", "accepted", "rejected", "review_required"]


class ResolverConfig(EvidenceModel):
    model_id: str = ""
    timeout_seconds: float = Field(default=30, gt=0, le=120)
    retries: int = Field(default=0, ge=0, le=2)
    concurrency: int = Field(default=1, ge=1, le=4)
    max_calls: int = Field(default=2, ge=0, le=20)
    max_input_tokens: int = Field(default=12000, gt=0)
    max_output_tokens: int = Field(default=2000, gt=0)
    total_token_budget: int = Field(default=30000, gt=0)
    max_nodes_per_request: int = Field(default=24, gt=0, le=100)
    allow_external: bool = False
    input_price_per_million: float | None = Field(default=None, ge=0)
    output_price_per_million: float | None = Field(default=None, ge=0)


class NumberMarker(EvidenceModel):
    family: str
    token: str
    ordinal: int | None = None


class SemanticFeature(EvidenceModel):
    node_id: str
    source_node_id: str | None
    source_cell_ids: tuple[str, ...]
    source_refs: tuple[SourceRef, ...]
    text_raw: str
    axis: Literal["row", "column", "document"]
    table_id: str | None
    section_id: str
    panel_id: str
    page_number: int
    order: tuple[int, int, int]
    row_range: tuple[int, int] | None = None
    column_range: tuple[int, int] | None = None
    physical_parent_id: str | None = None
    physical_evidence: Literal["observed", "inferred", "unresolved"]
    number: NumberMarker | None = None
    indent: float | None = None
    fonts: tuple[str, ...] = ()
    font_sizes: tuple[float, ...] = ()
    font_flags: tuple[int, ...] = ()
    bold: bool | None = None
    background_color: None = None
    role_candidates: tuple[str, ...] = ()
    repeated_node_ids: tuple[str, ...] = ()
    page_edge: Literal["start", "end", "interior"] = "interior"
    value_cell_ids: tuple[str, ...] = ()
    alignment_evidence: str = "unknown"


class RelationCandidate(EvidenceModel):
    candidate_id: str
    parent_id: str
    child_id: str
    relation_type: RelationType
    evidence_ids: tuple[str, ...]
    methods: tuple[str, ...]
    conflicts: tuple[str, ...] = ()
    relative_depth: int | None = None


class SemanticPatch(EvidenceModel):
    parent_id: str
    child_id: str
    relation_type: RelationType
    evidence_ids: list[str]
    reason: str


class SemanticPatchProposal(EvidenceModel):
    patches: list[SemanticPatch]
    abstain_node_ids: list[str]


class PatchDecision(EvidenceModel):
    proposal: SemanticPatch
    validation_status: Status
    reasons: tuple[str, ...]
    resolution_method: Literal["rule", "mock", "text_llm"]


class SemanticNode(EvidenceModel):
    semantic_node_id: str
    source_node_id: str | None
    source_cell_ids: tuple[str, ...]
    physical_parent_id: str | None
    semantic_parent_id: str | None
    relation_type: RelationType | None
    hierarchy_path: tuple[str, ...]
    semantic_role: str
    evidence: tuple[SourceRef, ...]
    resolution_method: str
    validation_status: Status
    unresolved_reason: str | None
    model_id: str | None
    run_id: UUID
    candidate_parent_ids: tuple[str, ...] = ()


class CellSnapshot(EvidenceModel):
    cell_id: str
    text_raw: str | None
    bbox: BBox
    physical_status: str


class CallRecord(EvidenceModel):
    request_id: str
    provider: str
    model_id: str
    status: str
    attempts: int
    elapsed_seconds: float
    input_tokens: int | None
    output_tokens: int | None
    input_token_upper_bound: int
    estimated_cost_usd: float | None
    request_sha256: str
    error_code: str | None = None


class ResolverRequest(EvidenceModel):
    request_id: str
    document_id: str
    mode: Literal["llm_only", "hybrid"]
    input_hashes: dict[str, str]
    nodes: tuple[SemanticFeature, ...]
    candidates: tuple[RelationCandidate, ...]
    section_context: tuple[str, ...]
    continuation_candidates: tuple[RelationCandidate, ...]
    target_node_ids: tuple[str, ...]
    allowed_node_ids: tuple[str, ...]
    allowed_evidence_ids: tuple[str, ...]


class SemanticOverlay(EvidenceModel):
    schema_version: Literal["semantic-overlay/1.0"] = "semantic-overlay/1.0"
    document_id: str
    accounting_scope: str
    scope_provenance: str
    run_id: UUID
    mode: Literal["rule_only", "llm_only", "hybrid"]
    model_id: str | None
    input_hashes: dict[str, str]
    features: tuple[SemanticFeature, ...]
    candidates: tuple[RelationCandidate, ...]
    decisions: tuple[PatchDecision, ...]
    table_nodes: tuple[SemanticNode, ...]
    document_nodes: tuple[SemanticNode, ...]
    source_cells: tuple[CellSnapshot, ...]
    calls: tuple[CallRecord, ...]
    escalations: tuple[str, ...]
    status: Literal["provisional", "requires_ocr_or_vision"]
    independently_reviewed: Literal[False] = False
