# 역할: 선 탐지부터 표 영역·격자·셀 복원·물리 검증까지 연결하는 기본 실행기다.
"""Phase 2-A orchestration. No semantic hierarchy or LLM dependencies."""

from uuid import uuid4

from doc_understanding.domain.models import Issue, SourceRef
from doc_understanding.parser.physical.boundary_reconstructor import CellBoundaryReconstructor
from doc_understanding.parser.physical.geometry import bbox, close_box, overlap
from doc_understanding.parser.physical.intersection_analyzer import GridIntersectionAnalyzer
from doc_understanding.parser.physical.line_detector import LineDetector
from doc_understanding.parser.physical.physical_models import GridBand, GridPolicy, PhysicalCell, PhysicalPageResult, PhysicalTableIR, SourceCellLink
from doc_understanding.parser.physical.physical_validator import PhysicalStructureValidator
from doc_understanding.parser.physical.region_detector import TableRegionDetector
from doc_understanding.parser.physical.region_assessor import assess_region
from doc_understanding.parser.physical.span_resolver import MergedCellSpanResolver


# 선 탐지·표 영역 선정·격자·셀 복원·검증을 연결하는 물리 처리 진입점이다.
class PhysicalGridReconstructor:
    # 동결 정책과 선택적 선 탐지기를 설정한다. 행열 의미 분석기는 포함하지 않는다.
    def __init__(self, policy=None, *, line_detector=None):
        self.policy = policy or GridPolicy()
        self.line_detector = line_detector

    # 한 페이지의 선과 원본 표 후보로 영역별 물리 표를 만든다.
    # 실패 진단과 어떤 영역에도 배정되지 않은 원본 셀도 페이지 결과에 보존한다.
    def analyze_page(self, document_id, page, raster=None, mode="auto", region_hints=()):
        if mode not in ("auto", "vector", "raster", "mixed"):
            raise ValueError("unknown line evidence mode")
        detector = self.line_detector or LineDetector(self.policy)
        vector = detector.vector(page) if mode != "raster" else ()
        use_raster = mode in ("raster", "mixed") or (mode == "auto" and not vector)
        lines = vector + (
            detector.raster(page, raster) if use_raster and raster is not None else ()
        )
        hints = tuple(region_hints) + tuple(t.source.bbox for t in page.tables)
        diagnostics = []
        try:
            regions = TableRegionDetector(self.policy).detect(
                lines, hints, tuple(s for b in page.blocks for s in b.spans))
        except ValueError as exc:
            return PhysicalPageResult(
                document_id=document_id,
                page_number=page.page_number,
                tables=(),
                diagnostics=(
                    Issue(
                        code=str(exc),
                        severity="error",
                        message="Physical analysis budget exceeded.",
                    ),
                ),
            )
        tables = []
        assessments = []
        for index, region in enumerate(regions):
            assessment = assess_region(region, page, self.policy)
            assessments.append(assessment)
            if assessment.decision == "rejected":
                diagnostics.append(
                    Issue(
                        code="NON_TABLE_REGION_REJECTED",
                        severity="info",
                        message=assessment.reason,
                        source_references=(
                            SourceRef(
                                document_id=document_id,
                                page_number=page.page_number,
                                bbox=region.bounding_box,
                            ),
                        ),
                    )
                )
                continue
            try:
                table = self._region(document_id, page, index, region)
                if region.recovery_codes:
                    table = table.model_copy(update={'validation_issues': table.validation_issues + tuple(
                        Issue(code=code,severity='warning',
                              message='Source-backed region candidate; original line evidence retained; not semantic approval.',
                              source_references=table.source_references)
                        for code in region.recovery_codes)})
                tables.append(table)
            except ValueError as exc:
                diagnostics.append(
                    Issue(code="REGION_ANALYSIS_FAILED", severity="error", message=str(exc))
                )
        if not regions:
            diagnostics.append(
                Issue(
                    code="NO_GRID_REGION_DETECTED",
                    severity="info",
                    message="No supported ruled table region; borderless tables may remain undetected.",
                )
            )
        accounted = {c.cell_id for table in tables for c in table.source_cells}
        unassigned = tuple(
            c for table in page.tables for c in table.cells if c.cell_id not in accounted
        )
        if unassigned:
            diagnostics.append(
                Issue(
                    code="SOURCE_CELLS_OUTSIDE_REGIONS",
                    severity="warning",
                    message="Original source cells retained outside reconstructed regions.",
                    source_references=tuple(c.source for c in unassigned),
                )
            )
        return PhysicalPageResult(
            document_id=document_id,
            page_number=page.page_number,
            tables=tuple(tables),
            unassigned_source_cells=unassigned,
            diagnostics=tuple(diagnostics),
            region_assessments=tuple(assessments),
        )

    # 영역 하나를 행·열 격자와 셀로 복원하고 원문 span·원본 셀 대응·경계 근거를 연결해 검증한다.
    def _region(self, document_id, page, index, region):
        tid = f"{page.page_id}/physical{index:03d}"
        grid = GridIntersectionAnalyzer(self.policy).analyze(
            region, tuple(s for b in page.blocks for s in b.spans)
        )
        rows = tuple(
            GridBand(index=i, start=a, end=b) for i, (a, b) in enumerate(zip(grid.ys, grid.ys[1:]))
        )
        columns = tuple(
            GridBand(index=i, start=a, end=b) for i, (a, b) in enumerate(zip(grid.xs, grid.xs[1:]))
        )
        raw_tables = tuple(
            t for t in page.tables if overlap(t.source.bbox, region.bounding_box) > 0
        )
        sources = tuple(
            c
            for t in raw_tables
            for c in t.cells
            if overlap(c.source.bbox, region.bounding_box) > 0
        )
        spans = tuple(s for b in page.blocks for s in b.spans)
        cells, links = [], []
        for ci, candidate in enumerate(CellBoundaryReconstructor().reconstruct(grid)):
            cb = bbox(
                grid.xs[candidate.column_start],
                grid.ys[candidate.row_start],
                grid.xs[candidate.column_end],
                grid.ys[candidate.row_end],
            )
            cid = f"{tid}/cell{ci:04d}"
            source_ids = tuple(c.cell_id for c in sources if overlap(c.source.bbox, cb) > 0)
            matched = [c for c in sources if close_box(c.source.bbox, cb, self.policy.tolerance)]
            observed_spans = tuple(
                s
                for s in spans
                if cb.x0 <= (s.source.bbox.x0 + s.source.bbox.x1) / 2 < cb.x1
                and cb.y0 <= (s.source.bbox.y0 + s.source.bbox.y1) / 2 < cb.y1
            )
            text = (
                matched[0].text_raw
                if len(matched) == 1
                else (
                    "\n".join(s.text_raw for s in observed_spans)
                    if page.text_status == "native"
                    else None
                )
            )
            rowspan, colspan = MergedCellSpanResolver().resolve(candidate)
            method = (
                "mixed_grid"
                if len({evidence.method for evidence in region.lines}) > 1
                else "raster_grid"
                if any(evidence.method == "raster" for evidence in region.lines)
                else "vector_grid"
            )
            cells.append(
                PhysicalCell(
                    cell_id=cid,
                    row_index=candidate.row_start,
                    column_index=candidate.column_start,
                    rowspan=rowspan,
                    colspan=colspan,
                    original_text=text,
                    source_bbox=cb,
                    source=SourceRef(
                        document_id=document_id, page_number=page.page_number, bbox=cb, cell_id=cid
                    ),
                    source_cell_ids=source_ids,
                    span_refs=tuple(s.span_id for s in observed_spans),
                    extraction_method=method if candidate.resolved else "unresolved",
                    status="inferred"
                    if candidate.inferred
                    else "resolved"
                    if candidate.resolved
                    else "unresolved",
                    boundary_ids=candidate.boundary_ids,
                    occupied_slots=candidate.slots,
                )
            )
            for source in sources:
                if source.cell_id in source_ids:
                    links.append(
                        SourceCellLink(
                            source_cell_id=source.cell_id,
                            reconstructed_cell_id=cid,
                            relation="exact"
                            if close_box(source.source.bbox, cb, self.policy.tolerance)
                            else "overlap",
                        )
                    )
        unassigned = tuple(
            c.cell_id
            for c in sources
            if not any(evidence.source_cell_id == c.cell_id for evidence in links)
        )
        links.extend(
            SourceCellLink(source_cell_id=s, reconstructed_cell_id=None, relation="unresolved")
            for s in unassigned
        )
        unresolved = tuple(
            b
            for b in grid.boundaries
            if b.status in ("uncertain", "inferred")
            or (
                b.status == "absent"
                and (
                    (b.axis == "horizontal" and b.row in (0, len(rows)))
                    or (b.axis == "vertical" and b.column in (0, len(columns)))
                )
            )
        )
        issue_list = []
        if any(b.status == "inferred" for b in grid.boundaries):
            issue_list.append(
                Issue(
                    code="INFERRED_BOUNDARY_EVIDENCE",
                    severity="warning",
                    message="Endpoint alignment or fill transition supports a candidate boundary; not an observed stroke.",
                )
            )
        if unresolved or any(c.status == "unresolved" for c in cells):
            issue_list.append(
                Issue(
                    code="BOUNDARY_UNRESOLVED",
                    severity="warning",
                    message="Boundary review required; inferred spans are provisional and unresolved spans remain null.",
                )
            )
        table = PhysicalTableIR(
            analyzer_version='physical-grid/0.3.0',
            table_id=tid,
            document_id=document_id,
            analysis_run_id=uuid4(),
            policy=self.policy,
            source_ir_sha256=page.evidence_sha256,
            page_number=page.page_number,
            bounding_box=region.bounding_box,
            rows=rows,
            columns=columns,
            cells=tuple(cells),
            source_cells=sources,
            source_links=tuple(links),
            unassigned_source_cell_ids=unassigned,
            table_candidate_ids=tuple(t.table_candidate_id for t in raw_tables),
            page_refs=(page.page_number,),
            source_references=(
                SourceRef(
                    document_id=document_id, page_number=page.page_number, bbox=region.bounding_box
                ),
            ),
            crop_refs=(page.image,),
            lines=region.lines,
            intersections=grid.intersections,
            boundaries=grid.boundaries,
            unresolved_boundaries=unresolved,
            validation_issues=tuple(issue_list),
        )
        return PhysicalStructureValidator().validate(table, page)
