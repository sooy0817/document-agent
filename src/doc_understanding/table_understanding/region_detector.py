"""Connected line geometry produces table regions, independent of text and page number."""

from dataclasses import dataclass

from doc_understanding.domain.models import BBox
from .geometry import bbox, cluster, overlap
from .physical_models import GridPolicy, LineSegment


@dataclass(frozen=True)
class TableRegion:
    bounding_box: BBox
    lines: tuple[LineSegment, ...]


def touches(a, b, tol):
    if a.axis == b.axis:
        return (
            abs(a.position - b.position) <= tol and max(a.start, b.start) <= min(a.end, b.end) + tol
        )
    h, v = (a, b) if a.axis == "horizontal" else (b, a)
    return h.start - tol <= v.position <= h.end + tol and v.start - tol <= h.position <= v.end + tol


class TableRegionDetector:
    def __init__(self, policy: GridPolicy):
        self.policy = policy

    def detect(self, lines: tuple[LineSegment, ...], hints: tuple[BBox, ...] = ()):
        if len(lines) > self.policy.max_lines:
            raise ValueError("LINE_BUDGET_EXCEEDED")
        parent = list(range(len(lines)))

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
            if len(xs) < 2 or len(ys) < 2 or max(len(xs), len(ys)) < 3:
                continue
            # Open-sided tables still include the full horizontal/vertical extent.
            # Endpoints define candidate extent, not evidence of a closed border.
            result.append(TableRegion(bbox(
                min(xs[0], *(h.start for h in hs)),
                min(ys[0], *(v.start for v in vs)),
                max(xs[-1], *(h.end for h in hs)),
                max(ys[-1], *(v.end for v in vs)),
            ), tuple(group)))
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
