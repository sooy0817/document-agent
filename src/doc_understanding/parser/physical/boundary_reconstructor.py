# 역할: 내부 경계가 완전히 없는 격자 구간만 연결하여 물리 셀 영역을 구성한다.
"""Join elementary slots only across fully absent interior edges."""

from dataclasses import dataclass


# 연결된 격자 슬롯의 범위와 경계 ID, 직사각형 복원·추론 여부를 담는다.
@dataclass(frozen=True)
class CellRegion:
    slots: tuple[tuple[int, int], ...]
    row_start: int
    row_end: int
    column_start: int
    column_end: int
    boundary_ids: tuple[str, ...]
    resolved: bool
    inferred: bool = False


# 격자의 경계 상태를 이용해 하나의 셀로 연결되는 슬롯을 묶는다.
class CellBoundaryReconstructor:
    # 경계가 absent인 이웃 슬롯을 연결하고 외곽·내부 경계와 직사각형 여부를 검사한다.
    # 확정할 수 없는 영역도 resolved=False로 남기며 임의 병합 span을 확정하지 않는다.
    def reconstruct(self, grid):
        rows, cols = len(grid.ys) - 1, len(grid.xs) - 1
        if rows < 1 or cols < 1:
            return ()
        edges = {(b.axis, b.row, b.column): b for b in grid.boundaries}
        remaining = {(r, c) for r in range(rows) for c in range(cols)}
        cells = []
        while remaining:
            first = min(remaining)
            remaining.remove(first)
            component, queue = {first}, [first]
            while queue:
                r, c = queue.pop()
                for nr, nc, key in [
                    (r - 1, c, ("horizontal", r, c)),
                    (r + 1, c, ("horizontal", r + 1, c)),
                    (r, c - 1, ("vertical", r, c)),
                    (r, c + 1, ("vertical", r, c + 1)),
                ]:
                    if (nr, nc) in remaining and edges[key].status == "absent":
                        remaining.remove((nr, nc))
                        component.add((nr, nc))
                        queue.append((nr, nc))
            r0, r1 = min(r for r, c in component), max(r for r, c in component) + 1
            c0, c1 = min(c for r, c in component), max(c for r, c in component) + 1
            rectangular = len(component) == (r1 - r0) * (c1 - c0)
            perimeter = []
            interior = []
            for r, c in component:
                for nr, nc, key in [
                    (r - 1, c, ("horizontal", r, c)),
                    (r + 1, c, ("horizontal", r + 1, c)),
                    (r, c - 1, ("vertical", r, c)),
                    (r, c + 1, ("vertical", r, c + 1)),
                ]:
                    (interior if (nr, nc) in component else perimeter).append(edges[key])
            resolved = (
                rectangular
                and all(e.status in ("present", "inferred") for e in perimeter)
                and all(e.status == "absent" for e in interior)
            )
            cells.append(
                CellRegion(
                    tuple(sorted(component)),
                    r0,
                    r1,
                    c0,
                    c1,
                    tuple(sorted({e.boundary_id for e in perimeter + interior})),
                    resolved,
                    resolved and any(e.status == "inferred" for e in perimeter),
                )
            )
        return tuple(cells)
