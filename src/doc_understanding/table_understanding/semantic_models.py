"""Implemented Phase 2-B contract; physical 2.1 and legacy semantic draft stay intact."""

from typing import Literal
from uuid import UUID

from doc_understanding.domain.models import BBox, EvidenceModel, Issue, SourceRef
from .physical_models import Boundary, PhysicalTableIR

EvidenceState = Literal["observed", "inferred", "unresolved"]


class HierarchyNode(EvidenceModel):
    node_id: str
    axis: Literal["row", "column"]
    panel_id: str
    label_raw: str | None
    parent_id: str | None = None
    candidate_parent_ids: tuple[str, ...] = ()
    source_cell_id: str
    bbox: BBox
    row_range: tuple[int, int]
    column_range: tuple[int, int]
    physical_evidence: EvidenceState
    evidence_state: EvidenceState = "inferred"
    boundary_ids: tuple[str, ...] = ()


class CellPath(EvidenceModel):
    cell_id: str
    bbox: BBox
    original_text: str | None
    panel_id: str
    row_path: tuple[str, ...] = ()
    column_path: tuple[str, ...] = ()
    row_candidates: tuple[str, ...] = ()
    column_candidates: tuple[str, ...] = ()
    evidence_state: EvidenceState


class CellRole(EvidenceModel):
    cell_id: str
    role: Literal["corner", "row_label", "column_header", "value", "unknown"]
    evidence_state: EvidenceState


class Ambiguity(EvidenceModel):
    ambiguity_id: str
    code: str
    reason: str
    target_ids: tuple[str, ...]
    candidate_ids: tuple[str, ...] = ()
    review_regions: tuple[SourceRef, ...]


class StructuralFeature(EvidenceModel):
    type: Literal[
        "multi_level_columns",
        "hierarchical_rows",
        "merged_cells",
        "side_by_side_panels",
        "continued_across_pages",
        "irregular_grid",
    ]
    disposition: Literal["observed", "candidate", "unknown"]
    evidence_cell_ids: tuple[str, ...]
    rule: str


class Panel(EvidenceModel):
    panel_id: str
    column_range: tuple[int, int]
    header_rows: int
    stub_columns: int
    evidence_state: EvidenceState = "inferred"


class StructuredTableIR(EvidenceModel):
    schema_version: Literal["structured-table-ir/2.0"] = "structured-table-ir/2.0"
    analyzer_version: Literal["semantic-geometry/0.1.0", "semantic-geometry/0.2.0"] = "semantic-geometry/0.1.0"
    table_id: str
    document_id: str
    page_number: int
    analysis_run_id: UUID
    physical_ir_sha256: str
    physical_schema_version: Literal["physical-table-ir/2.1"] = "physical-table-ir/2.1"
    physical_boundaries: tuple[Boundary, ...]
    physical_unresolved_boundaries: tuple[Boundary, ...]
    panels: tuple[Panel, ...]
    features: tuple[StructuralFeature, ...]
    row_tree: tuple[HierarchyNode, ...]
    column_tree: tuple[HierarchyNode, ...]
    cell_roles: tuple[CellRole, ...]
    cell_mappings: tuple[CellPath, ...]
    ambiguities: tuple[Ambiguity, ...]
    issues: tuple[Issue, ...]
    analysis_state: Literal["candidate", "partial", "review_required", "invalid"]
    independent_human_review: Literal["pending"] = "pending"


class VisionStructureRequest(EvidenceModel):
    """Future resolver input only. No execution or patch application in Phase 2-B."""

    schema_version: Literal["vision-structure-request/1.0"] = "vision-structure-request/1.0"
    physical: PhysicalTableIR
    structured: StructuredTableIR
    ambiguities: tuple[Ambiguity, ...]
    allowed_cell_ids: tuple[str, ...]
    allowed_node_ids: tuple[str, ...]
    allowed_operations: tuple[Literal["propose_parent", "propose_role", "abstain"], ...] = (
        "propose_parent",
        "propose_role",
        "abstain",
    )
    max_calls: Literal[0] = 0


class VisionStructureProposal(EvidenceModel):
    """Unapplied, untrusted proposal: no generated cell, box, number or text fields."""

    target_id: str
    operation: Literal["propose_parent", "propose_role", "abstain"]
    parent_id: str | None = None
    proposed_role: Literal["corner", "row_label", "column_header", "value", "unknown"] | None = None
    evidence_cell_ids: tuple[str, ...]
    reason: str
