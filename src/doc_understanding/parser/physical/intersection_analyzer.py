# 역할: 선 교차점과 원자 격자의 경계 근거를 계산하고 불확실한 경계를 보존한다.
"""Observed intersections and atomic edge coverage, with no gap completion."""

from dataclasses import dataclass

from doc_understanding.parser.physical.geometry import cluster, intervals_union
from doc_understanding.parser.physical.physical_models import Boundary, GridPolicy, Intersection


# 격자의 x/y 절단점, 교차점, 개별 슬롯 경계 근거를 함께 담는다.
@dataclass(frozen=True)
class GridGeometry:
    xs: tuple[float, ...]
    ys: tuple[float, ...]
    intersections: tuple[Intersection, ...]
    boundaries: tuple[Boundary, ...]


# 표 영역의 선을 격자로 해석하되 관찰 경계와 보완 추론을 구분한다.
class GridIntersectionAnalyzer:
    # 좌표 오차·격자 예산·작은 간격 보완에 사용할 동결 정책을 보관한다.
    def __init__(self, policy: GridPolicy):
        self.policy = policy

    # 선 좌표를 군집화하고 교차점·경계 피복률·텍스트 충돌을 계산한다.
    # 경계를 present/absent/uncertain/inferred로 구분한 GridGeometry를 반환한다.
    def analyze(self, region, spans=()) -> GridGeometry:
        tol = self.policy.tolerance
        hs = [evidence for evidence in region.lines if evidence.axis == "horizontal"]
        vs = [evidence for evidence in region.lines if evidence.axis == "vertical"]
        xs = cluster([evidence.position for evidence in vs], tol)
        ys = cluster([evidence.position for evidence in hs], tol)
        if xs and ys:
            # Preserve open outer strips without inventing supporting line evidence.
            box = region.bounding_box
            xs = cluster(
                [
                    *xs,
                    *([box.x0] if box.x0 < xs[0] - tol else []),
                    *([box.x1] if box.x1 > xs[-1] + tol else []),
                ],
                tol,
            )
            ys = cluster(
                [
                    *ys,
                    *([box.y0] if box.y0 < ys[0] - tol else []),
                    *([box.y1] if box.y1 > ys[-1] + tol else []),
                ],
                tol,
            )
        if max(0, len(xs) - 1) * max(0, len(ys) - 1) > self.policy.max_grid_slots:
            raise ValueError("GRID_BUDGET_EXCEEDED")
        intersections = []
        for ri, y in enumerate(ys):
            for ci, x in enumerate(xs):
                hit_h = [
                    evidence
                    for evidence in hs
                    if abs(evidence.position - y) <= tol
                    and evidence.start - tol <= x <= evidence.end + tol
                ]
                hit_v = [
                    evidence
                    for evidence in vs
                    if abs(evidence.position - x) <= tol
                    and evidence.start - tol <= y <= evidence.end + tol
                ]
                if hit_h and hit_v:
                    intersections.append(
                        Intersection(
                            intersection_id=f"i{ri}-{ci}",
                            x=x,
                            y=y,
                            line_ids=tuple(evidence.line_id for evidence in hit_h + hit_v),
                        )
                    )
        boundaries = []
        for axis, positions, varying, lines in [
            ("horizontal", ys, xs, hs),
            ("vertical", xs, ys, vs),
        ]:
            for i, position in enumerate(positions):
                for j, (start, end) in enumerate(zip(varying, varying[1:])):
                    relevant = [
                        evidence
                        for evidence in lines
                        if abs(evidence.position - position) <= tol
                        and min(end, evidence.end) - max(start, evidence.start) > tol
                    ]
                    intervals = intervals_union(
                        [
                            (max(start, evidence.start), min(end, evidence.end))
                            for evidence in relevant
                        ]
                    )
                    coverage = sum(b - a for a, b in intervals) / (end - start)
                    gaps = []
                    cursor = start
                    for a, b in intervals:
                        gaps.append(max(0, a - cursor))
                        cursor = max(cursor, b)
                    gaps.append(max(0, end - cursor))
                    status = (
                        "absent"
                        if not intervals
                        else "present"
                        if max(gaps) <= tol
                        else "uncertain"
                    )
                    conflicts = tuple(
                        s.span_id
                        for s in spans
                        if (
                            s.source.bbox.y0 + tol < position < s.source.bbox.y1 - tol
                            and min(end, s.source.bbox.x1) - max(start, s.source.bbox.x0) > tol
                            if axis == "horizontal"
                            else s.source.bbox.x0 + tol < position < s.source.bbox.x1 - tol
                            and min(end, s.source.bbox.y1) - max(start, s.source.bbox.y0) > tol
                        )
                    )
                    level = "observed" if status == "present" else "missing"
                    # Combine collinear intervals for the decision, retaining actual coverage/IDs.
                    if (
                        status == "uncertain"
                        and max(gaps) <= self.policy.max_small_gap
                        and coverage >= self.policy.minimum_gap_coverage
                        and not conflicts
                    ):
                        status, level = "present", "small_gap_bridged"
                    if status == "uncertain" and conflicts:
                        level = "text_conflict"
                    if status == "present" and relevant and all(
                        segment.evidence_kind == "fill_transition" for segment in relevant
                    ):
                        status, level = "inferred", "fill_transition"
                    # Infer only the outer extent from repeated line endpoints. This is NOT
                    # an observed stroke and cannot yield a validated table.
                    outer = i in (0, len(positions) - 1)
                    perpendicular = hs if axis == "vertical" else vs
                    endpoint_lines = [
                        segment
                        for segment in perpendicular
                        if min(abs(segment.start - position), abs(segment.end - position)) <= tol
                    ]
                    if (
                        status == "absent"
                        and outer
                        and self.policy.infer_open_outer
                        and not conflicts
                        and len(cluster([segment.position for segment in endpoint_lines], tol)) >= 3
                    ):
                        status, level = "inferred", "endpoint_alignment"
                        relevant = endpoint_lines
                    row, col = (i, j) if axis == "horizontal" else (j, i)
                    boundaries.append(
                        Boundary(
                            boundary_id=f"{axis[0]}{row}-{col}",
                            axis=axis,
                            row=row,
                            column=col,
                            position=position,
                            start=start,
                            end=end,
                            coverage=min(1, coverage),
                            status=status,
                            line_ids=tuple(evidence.line_id for evidence in relevant),
                            evidence_level=level,
                            text_conflict_span_ids=conflicts,
                        )
                    )
        return GridGeometry(xs, ys, tuple(intersections), tuple(boundaries))
