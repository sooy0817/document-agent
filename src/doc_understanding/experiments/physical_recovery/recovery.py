# 역할: 짧은 선·래스터·텍스트 정렬을 이용한 물리 복원 후보 알고리즘이다. 기본 파서와 분리한다.
"""Source-backed strategies, independent switches, no document-specific rules."""
from collections import defaultdict
from dataclasses import dataclass
from statistics import median

from doc_understanding.domain.models import Issue
from doc_understanding.parser.physical.geometry import overlap
from doc_understanding.parser.physical.line_detector import LineDetector
from doc_understanding.parser.physical.physical_grid import PhysicalGridReconstructor
from doc_understanding.parser.physical.physical_models import GridPolicy, PhysicalPageResult
from doc_understanding.parser.physical.region_assessor import assess_region
from doc_understanding.parser.physical.region_detector import TableRegionDetector
from doc_understanding.experiments.physical_recovery.text_grid import text_regions, reconstruct_text


@dataclass(frozen=True)
class Strategy:
    short: bool = False
    raster: bool = False
    text: bool = False

    @property
    def name(self):
        return ''.join(str(i) for i, v in enumerate((self.short, self.raster, self.text), 1) if v) or '0'


VARIANTS = tuple(Strategy(*x) for x in ((False, False, False), (True, False, False),
    (False, True, False), (False, False, True), (True, True, False), (True, False, True),
    (False, True, True), (True, True, True)))


def short_supported(page, base):
    """Keep actual fragments, not fictitious continuous strokes across text."""
    sizes = [s.font_size for b in page.blocks for s in b.spans if s.font_size > 0]
    scale = median(sizes) if sizes else 10
    candidate = LineDetector(GridPolicy(min_line_length=max(2, scale * .25))).vector(page)
    existing = {x.line_id for x in base}
    groups = defaultdict(list)
    for line in candidate:
        if line.axis == 'vertical':
            groups[round(line.position / .75)].append(line)
    supported = []
    for lines in groups.values():
        lines = sorted(lines, key=lambda x: x.start)
        # At least two neighboring fragments, supported length, no text crossing.
        for line in lines:
            neighbors = [x for x in lines if x.line_id != line.line_id
                         and min(abs(x.end-line.start), abs(line.end-x.start)) <= scale*.35]
            crosses = any(s.source.bbox.x0+.75 < line.position < s.source.bbox.x1-.75
                          and min(s.source.bbox.y1,line.end)-max(s.source.bbox.y0,line.start) > .75
                          for b in page.blocks for s in b.spans)
            if line.line_id not in existing and neighbors and not crosses:
                supported.append(line)
    # Neighboring double-rule boundaries can delimit an empty strip. The wider
    # tolerance is an experimental inference, recorded on every affected table.
    policy = GridPolicy(tolerance=min(2.5, max(.75, scale*.22))) if supported else GridPolicy()
    return tuple(base)+tuple(supported), policy, len(supported)


def needs_fill_raster(page, vectors):
    return not vectors and any(b.kind == 'drawing' and b.drawing_stroked is False for b in page.blocks)


class RecoveryAnalyzer:
    def analyze(self, document_id, page, strategy, baseline, raster_loader=None):
        if strategy.name == '0':
            return baseline, {'strategy': '0', 'baseline_reused': True}
        vectors = LineDetector(GridPolicy()).vector(page)
        lines, policy, short_count = vectors, GridPolicy(), 0
        if strategy.short:
            lines, policy, short_count = short_supported(page, vectors)
        raster_used = False
        if strategy.raster and needs_fill_raster(page, vectors) and raster_loader:
            lines = tuple(lines)+tuple(raster_loader())
            raster_used = True
        regions = TableRegionDetector(policy).detect(lines, tuple(t.source.bbox for t in page.tables))
        reconstructor = PhysicalGridReconstructor(policy)
        tables, assessments, diagnostics = [], [], []
        for region in regions:
            assessment = assess_region(region, page, policy)
            assessments.append(assessment)
            if assessment.decision == 'rejected':
                continue
            table = reconstructor._region(document_id, page, len(tables), region)
            if short_count:
                table = table.model_copy(update={
                    'cells': tuple(c.model_copy(update={'status':'inferred'})
                                   if c.status=='resolved' else c for c in table.cells),
                    'analysis_state': 'partial' if table.cells else 'review_required',
                    'validation_issues':table.validation_issues+(Issue(code='SHORT_FRAGMENT_RECOVERY_CANDIDATE',
                        severity='warning', message='Source fragments retained with repeated collinear support; adaptive tolerance needs review.'),)})
            tables.append(table)
        text_attempts = []
        if strategy.text:
            for proposal_index, box in enumerate(text_regions(page, lines)):
                overlapping = [t for t in tables if overlap(t.bounding_box, box) > .5 *
                               min((box.x1-box.x0)*(box.y1-box.y0),
                                   (t.bounding_box.x1-t.bounding_box.x0)*(t.bounding_box.y1-t.bounding_box.y0))]
                # Do not silently replace a usable ruled grid with a text hypothesis.
                if any(t.cells and t.columns and t.analysis_state != 'invalid' for t in overlapping):
                    continue
                table, reason = reconstruct_text(document_id, page, box, proposal_index)
                text_attempts.append({'bbox':box.xyxy, 'reason':reason})
                if table:
                    tables = [t for t in tables if t not in overlapping]
                    tables.append(table)
        for i,t in enumerate(tables):
            tables[i] = t.model_copy(update={'analyzer_version':'physical-recovery/0.1/S'+strategy.name})
        accounted = {c.cell_id for t in tables for c in t.source_cells}
        return PhysicalPageResult(document_id=document_id, page_number=page.page_number,
            tables=tuple(tables), region_assessments=tuple(assessments), diagnostics=tuple(diagnostics),
            unassigned_source_cells=tuple(c for t in page.tables for c in t.cells if c.cell_id not in accounted)), dict(
                strategy=strategy.name, short_fragments=short_count, tolerance=policy.tolerance,
                raster_used=raster_used, vector_lines=len(vectors), selected_lines=len(lines),
                text_attempts=text_attempts)
