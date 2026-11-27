"""Phase 2-6 PROPOSED boundaries. Models do not implement safety/publishing gates."""
from datetime import date
from decimal import Decimal
from typing import Literal, Protocol
from uuid import UUID

from doc_understanding.domain.models import ArtifactRef, DocumentIR, EvidenceModel, Issue, SourceRef
from doc_understanding.parser.contracts import CellMapping, ContextLink, HierarchyNode, StructuredTableIR
from doc_understanding.parser.physical.physical_models import PhysicalTableIR
from doc_understanding.parser.relations.logical_tables import LogicalTableLink
from doc_understanding.parser.structure.models import CellPath, HierarchyNode as GeometryNode, StructuredTableIR as GeometryTable


class Section(EvidenceModel):
    section_id: str
    parent_id: str | None
    title_raw: str
    source_references: tuple[SourceRef, ...]
    content_ids: tuple[str, ...]
    reading_order: int = 0
    evidence_state: Literal["inferred", "unresolved"] = "inferred"


class Content(EvidenceModel):
    content_id: str
    section_id: str | None
    kind: Literal["paragraph", "table", "note", "rule", "image", "unknown"]
    text_raw: str | None
    source_references: tuple[SourceRef, ...]
    reading_order: int = 0
    table_id: str | None = None
    context_content_ids: tuple[str, ...] = ()
    duplicate_of_table_ids: tuple[str, ...] = ()
    evidence_state: Literal["inferred", "unresolved"] = "inferred"


class SegmentedDocument(EvidenceModel):
    document: DocumentIR
    sections: tuple[Section, ...]
    contents: tuple[Content, ...]
    continuation_candidates: tuple[tuple[str, str], ...]


class TemplateVersion(EvidenceModel):
    template_id: str
    version: str
    level: Literal["document", "section", "content"]
    status: Literal["draft", "approved", "deprecated"]
    definition_json: str  # schema for definition finalized in Phase 3
    sha256: str


class MatchResult(EvidenceModel):
    target_id: str
    status: Literal["MATCHED", "PARTIAL", "UNKNOWN", "AMBIGUOUS", "UNSUPPORTED"]
    template: TemplateVersion | None
    evidence: tuple[SourceRef, ...]
    missing_requirements: tuple[str, ...]
    negative_evidence: tuple[str, ...]
    unmatched_source_ids: tuple[str, ...]


class RouteDecision(EvidenceModel):
    target_id: str
    route: Literal["deterministic", "guided", "generic", "review"]
    reason_codes: tuple[str, ...]
    reinterpret_source_ids: tuple[str, ...]


class StructurePatch(EvidenceModel):
    target_id: str
    operation: Literal["propose_parent", "propose_mapping", "propose_context_link"]
    proposed_parent_id: str | None = None
    proposed_row_id: str | None = None
    proposed_header_id: str | None = None
    evidence_cell_ids: tuple[str, ...]
    reason: str
    # Intentionally NO numeric values, raw text, coordinates or new source IDs.


class InterpretationRequest(EvidenceModel):
    source: DocumentIR
    structure: tuple[StructuredTableIR, ...]
    route: RouteDecision
    template: TemplateVersion | None
    allowed_source_ids: tuple[str, ...]
    crop_artifact_paths: tuple[str, ...]
    adjacent_page_numbers: tuple[int, ...]


class InterpretationResult(EvidenceModel):
    patches: tuple[StructurePatch, ...]
    unresolved_ids: tuple[str, ...]
    issues: tuple[Issue, ...]
    status: Literal["candidate", "timeout", "invalid_json", "provider_error", "review_required"]


class CanonicalDocument(EvidenceModel):
    schema_version: Literal["canonical-document/1.0-draft"] = "canonical-document/1.0-draft"
    document_id: str
    institution: str | None
    reporting_period: str | None
    sections: tuple[Section, ...]
    contents: tuple[Content, ...]
    table_structure: tuple[StructuredTableIR | GeometryTable, ...]
    column_hierarchy: tuple[HierarchyNode | GeometryNode, ...]
    row_hierarchy: tuple[HierarchyNode | GeometryNode, ...]
    cell_mappings: tuple[CellMapping | CellPath, ...]
    notes: tuple[Content, ...]
    source_references: tuple[SourceRef, ...]
    template_match: tuple[MatchResult, ...]
    validation_status: Literal["not_validated", "partial", "review_required", "validated"]
    issues: tuple[Issue, ...]
    # Additive document-level assembly, ADR-027. Original IRs remain unchanged.
    document_ir: DocumentIR | None = None
    physical_tables: tuple[PhysicalTableIR, ...] = ()
    context_links: tuple[ContextLink, ...] = ()
    reading_order_status: Literal["inferred", "unresolved"] = "unresolved"
    fact_publication_allowed: Literal[False] = False
    source_artifact_refs: tuple[ArtifactRef, ...] = ()
    source_uri: str | None = None  # caller acquisition metadata, never inferred from prose
    logical_table_links: tuple[LogicalTableLink, ...] = ()  # additive, unapproved source-ID paths


class FinancialFactCandidate(EvidenceModel):
    institution: str
    document_id: str
    run_id: UUID
    accounting_scope: Literal["integrated", "credit", "general", "unknown"]
    period_kind: Literal["instant", "duration", "comparison"]
    period_start: date | None
    period_end: date
    base_period_end: date | None
    item_path: tuple[str, ...]
    metric_id: str
    value: Decimal
    value_raw: str
    unit: str
    source: SourceRef
    validation_status: Literal["not_validated", "rejected", "approved"] = "not_validated"


class ValidationReport(EvidenceModel):
    run_id: UUID
    status: Literal["validated", "partial", "review_required", "invalid"]
    issues: tuple[Issue, ...]
    approved_observation_ids: tuple[str, ...]


class Segmenter(Protocol):
    def segment(self, document: DocumentIR) -> SegmentedDocument: ...


class TemplateRegistry(Protocol):
    def candidates(self, level: str) -> tuple[TemplateVersion, ...]: ...
    def get(self, template_id: str, version: str) -> TemplateVersion: ...


class TemplateMatcher(Protocol):
    def match(self, structure: StructuredTableIR, registry: TemplateRegistry) -> MatchResult: ...


class ExtractionRouter(Protocol):
    def route(self, structure: StructuredTableIR, match: MatchResult) -> RouteDecision: ...


class LLMVisionAdapter(Protocol):
    def interpret(self, request: InterpretationRequest) -> InterpretationResult: ...


class CanonicalValidator(Protocol):
    def validate(self, document: CanonicalDocument, evidence: DocumentIR) -> ValidationReport: ...


class DocumentRepository(Protocol):
    def save_snapshot(self, run_id: UUID, document: CanonicalDocument) -> None: ...
    def publish(self, facts: tuple[FinancialFactCandidate, ...], report: ValidationReport,
                idempotency_key: str) -> None: ...
