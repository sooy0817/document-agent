"""Physical geometry only; no account labels, templates or semantic hierarchy."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from doc_understanding.domain.models import (
    BBox,
    CellIR,
    EvidenceModel,
    Issue,
    NonEmpty,
    Positive,
    RasterRef,
    SourceRef,
)


class GridPolicy(EvidenceModel):
    tolerance: float = Field(default=0.75, gt=0, le=3)
    min_line_length: float = Field(default=12, gt=0)
    max_grid_slots: int = Field(default=20000, gt=0)
    raster_threshold: int = Field(default=100, ge=0, le=255)
    max_lines: int = Field(default=6000, gt=0)
    max_small_gap: float = Field(default=2.0, ge=0, le=3)
    minimum_gap_coverage: float = Field(default=0.94, ge=0, le=1)
    raster_strategy: Literal["legacy_dark", "adaptive_dark", "adaptive_contrast"] = (
        "adaptive_contrast"
    )
    raster_contrast: int = Field(default=12, ge=1, le=100)
    infer_open_outer: bool = True


class LineSegment(EvidenceModel):
    line_id: NonEmpty
    axis: Literal["horizontal", "vertical"]
    position: float
    start: float
    end: float
    source: SourceRef
    method: Literal["vector", "raster"]
    evidence_kind: Literal["stroke", "fill_transition"] = "stroke"


class GridBand(EvidenceModel):
    index: int = Field(ge=0)
    start: float
    end: float


class Intersection(EvidenceModel):
    intersection_id: str
    x: float
    y: float
    line_ids: tuple[str, ...]


class Boundary(EvidenceModel):
    boundary_id: str
    axis: Literal["horizontal", "vertical"]
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    position: float
    start: float
    end: float
    coverage: float = Field(ge=0, le=1)
    status: Literal["present", "absent", "uncertain", "inferred"]
    line_ids: tuple[str, ...]
    evidence_level: Literal[
        "observed",
        "small_gap_bridged",
        "endpoint_alignment",
        "text_conflict",
        "missing",
        "fill_transition",
    ] = "observed"
    text_conflict_span_ids: tuple[str, ...] = ()


class PhysicalCell(EvidenceModel):
    cell_id: NonEmpty
    row_index: int | None
    column_index: int | None
    rowspan: Positive | None
    colspan: Positive | None
    original_text: str | None
    source_bbox: BBox
    source: SourceRef
    source_cell_ids: tuple[str, ...] = ()
    span_refs: tuple[str, ...] = ()
    extraction_method: Literal["vector_grid", "raster_grid", "mixed_grid", "unresolved"]
    status: Literal["resolved", "inferred", "unresolved"]
    boundary_ids: tuple[str, ...] = ()
    occupied_slots: tuple[tuple[int, int], ...]


class SourceCellLink(EvidenceModel):
    source_cell_id: str
    reconstructed_cell_id: str | None
    relation: Literal["exact", "overlap", "unresolved"]


class PhysicalTableIR(EvidenceModel):
    schema_version: Literal["physical-table-ir/2.1"] = "physical-table-ir/2.1"
    table_id: NonEmpty
    document_id: NonEmpty
    analysis_run_id: UUID
    analyzer_version: str = "physical-grid/0.2.0"
    policy: GridPolicy
    source_ir_sha256: str
    page_number: Positive
    bounding_box: BBox
    rows: tuple[GridBand, ...]
    columns: tuple[GridBand, ...]
    cells: tuple[PhysicalCell, ...]
    source_cells: tuple[CellIR, ...]
    source_links: tuple[SourceCellLink, ...]
    unassigned_source_cell_ids: tuple[str, ...] = ()
    table_candidate_ids: tuple[str, ...] = ()
    page_refs: tuple[Positive, ...]
    source_references: tuple[SourceRef, ...]
    crop_refs: tuple[RasterRef, ...] = ()
    lines: tuple[LineSegment, ...]
    intersections: tuple[Intersection, ...]
    boundaries: tuple[Boundary, ...]
    unresolved_boundaries: tuple[Boundary, ...]
    validation_issues: tuple[Issue, ...] = ()
    analysis_state: Literal["candidate", "validated", "partial", "review_required", "invalid"] = (
        "candidate"
    )


class RegionAssessment(EvidenceModel):
    bounding_box: BBox
    decision: Literal["rejected", "retained"]
    reason: str
    features: dict[str, float]
    lines: tuple[LineSegment, ...] = ()


class PhysicalPageResult(EvidenceModel):
    document_id: str
    page_number: Positive
    tables: tuple[PhysicalTableIR, ...]
    unassigned_source_cells: tuple[CellIR, ...] = ()
    diagnostics: tuple[Issue, ...] = ()
    region_assessments: tuple[RegionAssessment, ...] = ()
