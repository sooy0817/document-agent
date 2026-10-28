"""Physical grid contract implemented; semantic contracts remain proposed."""

from typing import Literal, Protocol
from uuid import UUID

from doc_understanding.domain.models import (
    DocumentIR,
    EvidenceModel,
    Issue,
    NonEmpty,
    Positive,
    SourceRef,
)
from .physical_models import PhysicalTableIR

AnalysisState = Literal["candidate", "validated", "partial", "review_required", "invalid"]
MappingStatus = Literal["resolved", "ambiguous", "unresolved"]


class StructuralFeature(EvidenceModel):
    type: Literal[
        "multi_level_columns",
        "hierarchical_rows",
        "merged_cells",
        "side_by_side_panels",
        "continued_across_pages",
        "repeated_headers",
        "grouped_rows",
        "subtotals",
        "cross_tab",
        "mixed_table_narrative",
        "footnote_attached",
        "irregular_grid",
        "rotated_text",
    ]
    evidence_refs: tuple[SourceRef, ...]
    heuristic_id: NonEmpty
    disposition: Literal["observed", "candidate", "unknown"]


class Panel(EvidenceModel):
    panel_id: NonEmpty
    cell_ids: tuple[str, ...]


class HierarchyNode(EvidenceModel):
    node_id: NonEmpty
    panel_id: NonEmpty
    label_raw: str
    parent_id: str | None
    source_cell_ids: tuple[str, ...]
    kind: Literal["header", "detail", "group", "subtotal", "total", "unknown"]


class CellMapping(EvidenceModel):
    value_cell_id: NonEmpty
    panel_id: str | None
    row_node_id: str | None
    header_leaf_id: str | None
    status: MappingStatus


class ContextLink(EvidenceModel):
    source: SourceRef
    target_id: str | None
    scope: Literal["page", "section", "table", "row", "column", "cell", "unknown"]
    relation: Literal["note", "caption", "unit", "formula"]
    status: MappingStatus


class StructuredTableIR(EvidenceModel):
    schema_version: Literal["structured-table-ir/1.0"] = "structured-table-ir/1.0"
    table_id: NonEmpty
    document_id: NonEmpty
    analysis_run_id: UUID
    analyzer_version: NonEmpty
    source_ir_hash: NonEmpty
    table_candidate_ids: tuple[str, ...]
    pages: tuple[Positive, ...]
    analysis_state: AnalysisState = "candidate"
    features: tuple[StructuralFeature, ...] = ()
    panels: tuple[Panel, ...] = ()
    column_hierarchy: tuple[HierarchyNode, ...] = ()
    row_hierarchy: tuple[HierarchyNode, ...] = ()
    cell_mappings: tuple[CellMapping, ...] = ()
    context_links: tuple[ContextLink, ...] = ()
    unresolved_regions: tuple[SourceRef, ...] = ()
    issues: tuple[Issue, ...] = ()


class TableAnalysisRequest(EvidenceModel):
    document: DocumentIR
    candidates: tuple[PhysicalTableIR, ...]
    adjacent_page_refs: tuple[Positive, ...] = ()
    max_structure_llm_calls: Literal[0, 1] = 0


class TableAnalysisResult(EvidenceModel):
    analysis_run_id: UUID
    structured_tables: tuple[StructuredTableIR, ...]
    unresolved_regions: tuple[SourceRef, ...]
    issues: tuple[Issue, ...]


class PhysicalStructureAnalyzer(Protocol):
    def analyze(self, request: TableAnalysisRequest) -> tuple[PhysicalTableIR, ...]: ...


class StructuralFeatureClassifier(Protocol):
    def classify(self, table: PhysicalTableIR) -> tuple[StructuralFeature, ...]: ...


class SemanticStructureResolver(Protocol):
    def resolve(
        self, table: PhysicalTableIR, features: tuple[StructuralFeature, ...]
    ) -> StructuredTableIR: ...


class StructureValidator(Protocol):
    def validate(
        self, physical: PhysicalTableIR, structured: StructuredTableIR
    ) -> StructuredTableIR: ...


class TableStructureAnalyzer(Protocol):
    def analyze(self, request: TableAnalysisRequest) -> TableAnalysisResult: ...
