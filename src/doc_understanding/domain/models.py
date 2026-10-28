"""Phase 1 evidence only. Schema validation is NOT financial validation."""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonEmpty = Annotated[str, Field(min_length=1)]
SHA256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Positive = Annotated[int, Field(gt=0)]
Index = Annotated[int, Field(ge=0)]


class EvidenceModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class BBox(EvidenceModel):
    """Unrotated visible page, PDF points, top-left origin (72 pt/in)."""
    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError("bbox endpoints must be ordered")
        return self

    @property
    def xyxy(self) -> tuple[float, float, float, float]:
        return self.x0, self.y0, self.x1, self.y1


class SourceRef(EvidenceModel):
    document_id: NonEmpty
    page_number: Positive
    bbox: BBox
    block_id: str | None = None
    span_id: str | None = None
    cell_id: str | None = None


class Issue(EvidenceModel):
    code: NonEmpty
    severity: Literal["info", "warning", "error"]
    message: NonEmpty
    source_references: tuple[SourceRef, ...] = ()


class ArtifactRef(EvidenceModel):
    path: NonEmpty  # relative to the run directory, never a public URL
    sha256: SHA256
    media_type: NonEmpty


class RasterRef(EvidenceModel):
    artifact: ArtifactRef
    width_pixels: Positive
    height_pixels: Positive
    dpi: Positive
    # x_px = a*x_pt+c*y_pt+e, y_px = b*x_pt+d*y_pt+f
    page_to_pixel: tuple[float, float, float, float, float, float]
    clip: BBox

    @model_validator(mode="after")
    def invertible(self) -> Self:
        a, b, c, d, _, _ = self.page_to_pixel
        if not all(math.isfinite(x) for x in self.page_to_pixel) or abs(a*d-b*c) < 1e-12:
            raise ValueError("raster transform must be finite and invertible")
        return self


class TextSpanIR(EvidenceModel):
    span_id: NonEmpty
    source: SourceRef
    text_raw: str
    line_index: Index
    font: str
    font_size: Annotated[float, Field(ge=0)]
    flags: int
    direction: tuple[float, float] = (1.0, 0.0)


class DrawingCommand(EvidenceModel):
    operator: NonEmpty
    coordinates: tuple[float, ...]


class BlockIR(EvidenceModel):
    block_id: NonEmpty
    kind: Literal["text", "image", "drawing", "table_candidate"]
    source: SourceRef
    extraction_order: Index  # engine order, not semantic reading order
    text_raw: str | None = None
    spans: tuple[TextSpanIR, ...] = ()
    drawing_commands: tuple[DrawingCommand, ...] = ()
    drawing_stroked: bool | None = None
    image: ArtifactRef | None = None
    table_ref: str | None = None
    extraction_method: str = "pdf_native"
    confidence: None = None  # no calibrated probability in Phase 1


class CellIR(EvidenceModel):
    cell_id: NonEmpty
    source: SourceRef
    row_index: Index | None = None
    column_index: Index | None = None
    rowspan: Positive | None = None
    colspan: Positive | None = None
    text_raw: str | None  # None is unreadable/unavailable, "" is observed blank
    span_refs: tuple[str, ...] = ()  # geometric candidates; not semantic bindings
    extraction_status: Literal["observed", "blank", "unreadable"]

    @model_validator(mode="after")
    def raw_status(self) -> Self:
        if self.extraction_status == "blank" and self.text_raw != "":
            raise ValueError("blank cells must have an explicit empty string")
        if self.extraction_status == "unreadable" and self.text_raw is not None:
            raise ValueError("unreadable cells must preserve unavailable text as null")
        if self.extraction_status == "observed" and self.text_raw in (None, ""):
            raise ValueError("observed cells require source text")
        return self


class TableCandidateIR(EvidenceModel):
    table_candidate_id: NonEmpty
    source: SourceRef
    cells: tuple[CellIR, ...]
    # Detector's rectangular slots: null != empty cell. Null may be covered by a merge.
    detector_grid: tuple[tuple[str | None, ...], ...]
    row_count: Index
    column_count: Index
    issues: tuple[Issue, ...] = ()

    @model_validator(mode="after")
    def grid_refs(self) -> Self:
        ids = [c.cell_id for c in self.cells]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate physical cell ID")
        if len(self.detector_grid) != self.row_count:
            raise ValueError("detector row count mismatch")
        refs = []
        for row in self.detector_grid:
            if len(row) != self.column_count:
                raise ValueError("detector column count mismatch")
            refs.extend(x for x in row if x is not None)
        if set(refs) != set(ids):
            raise ValueError("detector grid must reference exactly its observed cells")
        return self


class PageIR(EvidenceModel):
    page_id: NonEmpty
    page_number: Positive
    width: Annotated[float, Field(gt=0)]
    height: Annotated[float, Field(gt=0)]
    original_rotation: Literal[0, 90, 180, 270]
    original_cropbox: BBox
    evidence_sha256: SHA256  # hash of canonical native evidence snapshot, not PDF page bytes
    image: RasterRef
    blocks: tuple[BlockIR, ...] = ()
    tables: tuple[TableCandidateIR, ...] = ()
    text_status: Literal["native", "ocr_required", "empty"]
    issues: tuple[Issue, ...] = ()


class DocumentMetadata(EvidenceModel):
    institution: str | None = None
    reporting_period: str | None = None
    period_end: date | None = None
    accounting_scope: Literal["integrated", "credit", "general", "unknown"] = "unknown"
    provenance: Literal["caller_supplied", "unknown"] = "unknown"


class ExtractionRun(EvidenceModel):
    run_id: UUID = Field(default_factory=uuid4)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    parser_version: NonEmpty
    engine: NonEmpty
    engine_version: NonEmpty
    policy_version: str = "extraction-policy/1.0"


class DocumentIR(EvidenceModel):
    schema_version: Literal["document-ir/1.0"] = "document-ir/1.0"
    document_id: NonEmpty
    source_name: NonEmpty
    source_sha256: SHA256
    source: ArtifactRef
    metadata: DocumentMetadata = Field(default_factory=DocumentMetadata)
    bbox_space: Literal["unrotated_cropbox_pdf_points_top_left"] = "unrotated_cropbox_pdf_points_top_left"
    pages: tuple[PageIR, ...]
    run: ExtractionRun
    processing_status: Literal["extracted", "review_required"]
    validation_status: Literal["not_validated"] = "not_validated"
    issues: tuple[Issue, ...] = ()

    @model_validator(mode="after")
    def lineage(self) -> Self:
        if self.document_id != f"sha256:{self.source_sha256}":
            raise ValueError("document_id must identify the original PDF bytes")
        if self.source.sha256 != self.source_sha256:
            raise ValueError("source artifact hash mismatch")
        if not self.pages or [p.page_number for p in self.pages] != list(range(1, len(self.pages)+1)):
            raise ValueError("pages must be complete and ordered, numbered from 1")
        ids: set[str] = set()
        for p in self.pages:
            def unique(node_id: str) -> None:
                if node_id in ids:
                    raise ValueError("duplicate evidence ID")
                ids.add(node_id)

            def check(ref: SourceRef) -> None:
                if ref.document_id != self.document_id or ref.page_number != p.page_number:
                    raise ValueError("source document/page mismatch")

            unique(p.page_id)
            span_ids = set()
            for b in p.blocks:
                unique(b.block_id)
                check(b.source)
                if b.source.block_id != b.block_id:
                    raise ValueError("block source ID mismatch")
                for s in b.spans:
                    unique(s.span_id)
                    span_ids.add(s.span_id)
                    check(s.source)
                    if s.source.span_id != s.span_id or s.source.block_id != b.block_id:
                        raise ValueError("span source ID mismatch")
            table_ids = {t.table_candidate_id for t in p.tables}
            for b in p.blocks:
                if b.kind == "table_candidate" and b.table_ref not in table_ids:
                    raise ValueError("dangling table block")
            for t in p.tables:
                unique(t.table_candidate_id)
                check(t.source)
                for c in t.cells:
                    unique(c.cell_id)
                    check(c.source)
                    if c.source.cell_id != c.cell_id or not set(c.span_refs) <= span_ids:
                        raise ValueError("cell provenance references invalid evidence")
        return self
