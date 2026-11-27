# 역할: 연결된 선과 원문 위치를 이용하여 독립적인 표 영역 후보를 찾는다.
"""Connected line geometry produces table regions, independent of text and page number."""

from dataclasses import dataclass

from doc_understanding.domain.models import BBox
from doc_understanding.parser.physical.geometry import bbox, cluster, overlap, close_box, intervals_union
from doc_understanding.parser.physical.physical_models import GridPolicy, LineSegment


# 표 후보의 경계 상자와 원본 선, 영역 복원 사유를 담는다.
@dataclass(frozen=True)
class TableRegion:
    bounding_box: BBox
    lines: tuple[LineSegment, ...]
    recovery_codes: tuple[str, ...] = ()


# 두 선이 허용 오차 안에서 이어지거나 교차하는지 검사한다.
def touches(a, b, tol):
    if a.axis == b.axis:
        return (
            abs(a.position - b.position) <= tol and max(a.start, b.start) <= min(a.end, b.end) + tol
        )
    h, v = (a, b) if a.axis == "horizontal" else (b, a)
    return h.start - tol <= v.position <= h.end + tol and v.start - tol <= h.position <= v.end + tol


# 연결된 선과 원본 영역 힌트로 표 후보 영역을 찾는다.
class TableRegionDetector:
    # 선 수 예산과 좌표 허용 오차 등 영역 검출 정책을 보관한다.
    def __init__(self, policy: GridPolicy):
        self.policy = policy

    # 반복 끝점과 내부 선 피복으로 열린 외곽 범위를 지지하는지 검사한다. 외곽 stroke를 생성하지 않는다.
    def _open_sides(self, hs, vs, xs, ys):
        """Repeated endpoints support extent, not fabricated outer strokes."""
        tol = self.policy.tolerance
        if not self.policy.infer_open_outer or len(xs) != 1 or len(ys) < 3:
            return False
        left, right = min(h.start for h in hs), max(h.end for h in hs)
        if not left + tol < xs[0] < right - tol:
            return False
        # Segmented horizontal strokes may meet at the divider. Compare their
        # complete per-row extent, not the individual short segments.
        for y in ys:
            runs = intervals_union([(h.start, h.end) for h in hs if abs(h.position-y) <= tol])
            if abs(runs[0][0]-left) > tol or abs(runs[-1][1]-right) > tol:
                return False
            if any(b[0]-a[1] > tol for a,b in zip(runs,runs[1:])):
                return False
        runs = intervals_union([(v.start,v.end) for v in vs])
        coverage = sum(max(0,min(b,ys[-1])-max(a,ys[0])) for a,b in runs)/(ys[-1]-ys[0])
        return coverage >= self.policy.minimum_gap_coverage

    # 원본 근거가 있는 위아래 영역 쌍을 연결하되 실제 선 정보는 보존한다.
    def _join_fragments(self, regions, hints, spans):
        """Join a complete source-backed pair; keep every actual line unchanged."""
        tol = self.policy.tolerance
        result = list(regions)
        proposals = []
        for i,a in enumerate(result):
            for b in result[i+1:]:
                top,bottom = sorted((a,b), key=lambda r:r.bounding_box.y0)
                x,y = top.bounding_box,bottom.bounding_box
                if not 0 < y.y0-x.y1 <= self.policy.max_small_gap:
                    continue
                if abs(x.x0-y.x0)>tol or abs(x.x1-y.x1)>tol:
                    continue
                profile = lambda r: cluster([l.position for l in r.lines if l.axis=='vertical'],tol)
                px,py = profile(top),profile(bottom)
                if len(px)<3 or len(px)!=len(py) or any(abs(u-v)>tol for u,v in zip(px,py)):
                    continue
                combined = bbox(min(x.x0,y.x0),x.y0,max(x.x1,y.x1),y.y1)
                if not any(close_box(h,combined,tol*2) for h in hints):
                    continue
                # A title/narrative in the gap contradicts a single grid.
                if any(s.text_raw.strip() and s.source.bbox.y0 < y.y0
                       and s.source.bbox.y1 > x.y1 and s.source.bbox.x0 < x.x1
                       and s.source.bbox.x1 > x.x0 for s in spans):
                    continue
                proposals.append((top,bottom,TableRegion(combined,top.lines+bottom.lines,
                    ('SOURCE_ALIGNED_FRAGMENT_JOIN',))))
        # Competing pairings remain separate, rather than depending on iteration order.
        for top,bottom,joined in proposals:
            if any((top is a or top is b or bottom is a or bottom is b)
                   for a,b,_ in proposals if a is not top or b is not bottom):
                continue
            result.remove(top);result.remove(bottom);result.append(joined)
        return result

    # 선 연결 성분으로 영역을 만들고 원본 힌트로 빠진 영역을 보완해 위치순으로 반환한다.
    def detect(self, lines: tuple[LineSegment, ...], hints: tuple[BBox, ...] = (), spans=()):
        if len(lines) > self.policy.max_lines:
            raise ValueError("LINE_BUDGET_EXCEEDED")
        parent = list(range(len(lines)))

        # 연결 성분의 대표를 찾고 경로를 압축한다. 의미 트리의 부모 탐색과는 별개다.
        def root(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i, a in enumerate(lines):
            for j in range(i):
                if touches(a, lines[j], self.policy.tolerance):
                    parent[root(i)] = root(j)
        groups = {}
        for i, line in enumerate(lines):
            groups.setdefault(root(i), []).append(line)
        result = []
        for group in groups.values():
            hs = [evidence for evidence in group if evidence.axis == "horizontal"]
            vs = [evidence for evidence in group if evidence.axis == "vertical"]
            ys = cluster([evidence.position for evidence in hs], self.policy.tolerance)
            xs = cluster([evidence.position for evidence in vs], self.policy.tolerance)
            regular = len(xs) >= 2 and len(ys) >= 2 and max(len(xs),len(ys)) >= 3
            if not regular and not self._open_sides(hs,vs,xs,ys):
                continue
            # Open-sided tables still include the full horizontal/vertical extent.
            # Endpoints define candidate extent, not evidence of a closed border.
            result.append(TableRegion(bbox(
                min(xs[0], *(h.start for h in hs)),
                min(ys[0], *(v.start for v in vs)),
                max(xs[-1], *(h.end for h in hs)),
                max(ys[-1], *(v.end for v in vs)),
            ), tuple(group), () if regular else ('OPEN_SIDED_REGION_CANDIDATE',)))
        result = self._join_fragments(result,hints,spans)
        for hint in hints:
            if not any(overlap(hint, r.bounding_box) > 0 for r in result):
                selected = tuple(
                    evidence
                    for evidence in lines
                    if (
                        hint.y0 - self.policy.tolerance
                        <= evidence.position
                        <= hint.y1 + self.policy.tolerance
                        and evidence.end >= hint.x0
                        and evidence.start <= hint.x1
                    )
                    if evidence.axis == "horizontal"
                )
                selected += tuple(
                    evidence
                    for evidence in lines
                    if evidence.axis == "vertical"
                    and hint.x0 - self.policy.tolerance
                    <= evidence.position
                    <= hint.x1 + self.policy.tolerance
                    and evidence.end >= hint.y0
                    and evidence.start <= hint.y1
                )
                result.append(TableRegion(hint, selected))
        return tuple(sorted(result, key=lambda r: (r.bounding_box.y0, r.bounding_box.x0)))
