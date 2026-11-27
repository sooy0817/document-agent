# 역할: 선택 실행 시 표와 연결 도형을 구분해 의미 분석 진행 여부를 제안한다. 기본 물리 IR은 보존한다.
# 역할: 선 연결과 닫힌 영역의 기하 근거로 도식 후보를 검사하는 선택적 진입 게이트다.
"""Opt-in geometry gate. No source mutation, text interpretation or fact approval.

This is deliberately a narrow diagram *candidate* detector, not a diagram reader.
It runs after physical reconstruction and before optional semantic analysis.
"""

from typing import Literal

from doc_understanding.domain.models import BBox, EvidenceModel
from doc_understanding.parser.physical.geometry import intervals_union
from doc_understanding.parser.physical.line_detector import LineDetector
from doc_understanding.parser.physical.physical_models import GridPolicy


# 닫힌 경계·공유 선·연결선 판단에 사용할 선택적 분류 임계값이다.
class GatePolicy(EvidenceModel):
    tolerance: float = 1.5
    side_coverage: float = 0.94
    minimum_connectors: int = 2


# 서로 떨어진 상자 사이를 연결하는 선의 출처 정보를 담는다.
class Connector(EvidenceModel):
    source_line_id: str
    source_block_id: str
    from_cell_id: str
    to_cell_id: str
    axis: Literal["horizontal", "vertical"]


# 표별 분류·진행/보류 결정과 닫힌 셀·연결선 근거를 기록한다.
class GateDecision(EvidenceModel):
    table_id: str
    page_number: int
    bbox: BBox
    classification: Literal["table_candidate", "diagram_candidate", "mixed_or_unknown"]
    action: Literal["continue_semantic_candidate", "skip_table_semantics", "hold_table_semantics"]
    reasons: tuple[str, ...]
    closed_cell_ids: tuple[str, ...]
    connectors: tuple[Connector, ...]
    source_span_ids: tuple[str, ...]
    shared_boundary_pairs: int
    source_preserved: Literal[True] = True
    semantic_status: Literal["not_validated"] = "not_validated"


# 한 페이지의 분류 정책과 표별 결정을 묶는다.
class PageGate(EvidenceModel):
    schema_version: Literal["diagram-gate/1.0"] = "diagram-gate/1.0"
    page_number: int
    policy: GatePolicy
    decisions: tuple[GateDecision, ...]


# 같은 축·위치의 선이 대상 구간을 덮는 비율을 중복 없이 계산한다.
def _coverage(lines, axis, pos, start, end, tol):
    spans = [
        (max(start, x.start), min(end, x.end))
        for x in lines
        if x.axis == axis and abs(x.position - pos) <= tol and min(end, x.end) > max(start, x.start)
    ]
    return sum(b - a for a, b in intervals_union(spans)) / max(end - start, 1e-9)


# 상자의 네 변이 모두 정책상 충분한 선 근거로 닫혀 있는지 검사한다.
def _closed(box, lines, policy):
    return (
        min(
            _coverage(lines, axis, pos, lo, hi, policy.tolerance)
            for axis, pos, lo, hi in (
                ("horizontal", box.y0, box.x0, box.x1),
                ("horizontal", box.y1, box.x0, box.x1),
                ("vertical", box.x0, box.y0, box.y1),
                ("vertical", box.x1, box.y0, box.y1),
            )
        )
        >= policy.side_coverage
    )


# 두 상자가 충분한 길이의 변을 공유하며 인접하는지 검사한다.
def _adjacent(a, b, tol):
    vertical = abs(a.x1 - b.x0) <= tol or abs(b.x1 - a.x0) <= tol
    horizontal = abs(a.y1 - b.y0) <= tol or abs(b.y1 - a.y0) <= tol
    return (vertical and min(a.y1, b.y1) - max(a.y0, b.y0) > 2 * tol) or (
        horizontal and min(a.x1, b.x1) - max(a.x0, b.x0) > 2 * tol
    )


# 공유 격자선과 달리 두 상자 사이의 빈 간격을 잇는 연결선인지 검사한다.
def _bridge(line, a, b, tol):
    """A line terminates at side interiors across a gap, not a shared grid edge."""
    if line.axis == "horizontal":
        gap = b.x0 - a.x1
        return (
            gap > 2 * tol
            and abs(line.start - a.x1) <= tol
            and abs(line.end - b.x0) <= tol
            and max(a.y0, b.y0) + tol < line.position < min(a.y1, b.y1) - tol
        )
    gap = b.y0 - a.y1
    return (
        gap > 2 * tol
        and abs(line.start - a.y1) <= tol
        and abs(line.end - b.y0) <= tol
        and max(a.x0, b.x0) + tol < line.position < min(a.x1, b.x1) - tol
    )


# 닫힌 셀·공유 경계·연결선 근거로 표별 분류와 의미 분석 진행/보류 결정을 만든다.
def classify_page(page, tables, policy=GatePolicy()):
    # Reuse vector line conversion. Fill-transition evidence already present in
    # PhysicalTableIR.lines is also accepted; no new raster/OCR extraction.
    source_lines = {x.line_id: x for x in LineDetector(GridPolicy()).vector(page)}
    for table in tables:
        source_lines.update({x.line_id: x for x in table.lines})
    lines = tuple(source_lines.values())
    decisions = []
    for table in tables:
        b = table.bounding_box
        local = [
            x
            for x in lines
            if x.axis == "horizontal"
            and (
                b.y0 - policy.tolerance <= x.position <= b.y1 + policy.tolerance
                and x.end >= b.x0
                and x.start <= b.x1
            )
        ]
        local += [
            x
            for x in lines
            if x.axis == "vertical"
            and b.x0 - policy.tolerance <= x.position <= b.x1 + policy.tolerance
            and x.end >= b.y0
            and x.start <= b.y1
        ]
        closed = [c for c in table.cells if _closed(c.source_bbox, local, policy)]
        bridges, adjacent = [], []
        for i, a in enumerate(closed):
            for j, c in enumerate(closed[:i]):
                if _adjacent(a.source_bbox, c.source_bbox, policy.tolerance):
                    adjacent.append((i, j))
        for line in local:
            starts = [
                c
                for c in closed
                if abs(
                    line.start
                    - (c.source_bbox.x1 if line.axis == "horizontal" else c.source_bbox.y1)
                )
                <= policy.tolerance
            ]
            ends = [
                c
                for c in closed
                if abs(
                    line.end - (c.source_bbox.x0 if line.axis == "horizontal" else c.source_bbox.y0)
                )
                <= policy.tolerance
            ]
            for a in starts:
                for c in ends:
                    if a.cell_id != c.cell_id and _bridge(
                        line, a.source_bbox, c.source_bbox, policy.tolerance
                    ):
                        bridges.append(
                            Connector(
                                source_line_id=line.line_id,
                                source_block_id=line.source.block_id or line.line_id,
                                from_cell_id=a.cell_id,
                                to_cell_id=c.cell_id,
                                axis=line.axis,
                            )
                        )
        # Duplicate drawing commands cannot inflate the required connector count.
        distinct = {(x.from_cell_id, x.to_cell_id, x.axis) for x in bridges}
        nodes = {x for a, b, _ in distinct for x in (a, b)}
        connected = set(nodes)
        # Header/body cells sharing an edge belong to the same visual box group.
        for _ in range(len(closed)):
            for i, j in adjacent:
                a, b = closed[i].cell_id, closed[j].cell_id
                if a in connected or b in connected:
                    connected.update((a, b))
        reasons = []
        if len(distinct) >= policy.minimum_connectors and len(nodes) >= 3:
            reasons.append("SEPARATED_CLOSED_CELLS_WITH_MULTIPLE_GAP_CONNECTORS")
            if any(c.cell_id not in connected for c in closed):
                kind, action = "mixed_or_unknown", "hold_table_semantics"
                reasons.append("OTHER_CLOSED_CELLS_PRESENT_DO_NOT_DROP_WHOLE_REGION")
            else:
                kind, action = "diagram_candidate", "skip_table_semantics"
        elif bridges:
            kind, action = "mixed_or_unknown", "hold_table_semantics"
            reasons.append("INSUFFICIENT_OR_CONFLICTING_CONNECTOR_EVIDENCE")
        elif adjacent:
            kind, action = "table_candidate", "continue_semantic_candidate"
            reasons.append("SHARED_CLOSED_CELL_BOUNDARIES_NOT_SEMANTIC_APPROVAL")
        else:
            kind, action = "mixed_or_unknown", "hold_table_semantics"
            reasons.append("INSUFFICIENT_CLOSED_GRID_EVIDENCE")
        decisions.append(
            GateDecision(
                table_id=table.table_id,
                page_number=page.page_number,
                bbox=table.bounding_box,
                classification=kind,
                action=action,
                reasons=tuple(reasons),
                closed_cell_ids=tuple(c.cell_id for c in closed),
                connectors=tuple(bridges),
                source_span_ids=tuple(sorted({s for c in table.cells for s in c.span_refs})),
                shared_boundary_pairs=len(adjacent),
            )
        )
    return PageGate(page_number=page.page_number, policy=policy, decisions=tuple(decisions))


# 명시적으로 선택한 경우에만 허용된 표에 analyzer를 실행한다. 제외한 표의 기준 IR은 그대로 남는다.
def analyze_allowed(tables, gate, analyzer):
    """Explicit opt-in dispatch; baseline IR remains available for every skipped ID."""
    decisions = {d.table_id: d for d in gate.decisions}
    if set(decisions) != {t.table_id for t in tables}:
        raise ValueError("GATE_TABLE_ID_MISMATCH")
    return tuple(
        analyzer.analyze(t)
        for t in tables
        if decisions[t.table_id].action == "continue_semantic_candidate"
    )
