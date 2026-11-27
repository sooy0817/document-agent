# 역할: 셀과 행 구간의 기하적 점유 관계를 계산하며 부모·자식 의미를 추정하지 않는다.
"""Geometric occupancy only; no parent/category inference."""


# 셀 상자와 물리 행·열 구간의 기하 대응을 계산한다.
class CellToRowAlignment:
    # 허용 오차보다 크게 겹치는 물리 행·열의 인덱스 목록을 반환한다.
    def align(self, cell_bbox, rows, columns, tolerance):
        rs = [
            r.index
            for r in rows
            if min(r.end, cell_bbox.y1) - max(r.start, cell_bbox.y0) > tolerance
        ]
        cs = [
            c.index
            for c in columns
            if min(c.end, cell_bbox.x1) - max(c.start, cell_bbox.x0) > tolerance
        ]
        return tuple(rs), tuple(cs)

    # resolved 셀끼리 점유 행 범위의 포함 여부를 비교한다. 미해결이면 None이며 의미 부모를 확정하지 않는다.
    @staticmethod
    def row_range_contains(group, member):
        """Compare occupied row intervals. Does not assert a semantic parent relation."""
        if group.status != "resolved" or member.status != "resolved":
            return None
        return (
            group.row_index <= member.row_index
            and group.row_index + group.rowspan >= member.row_index + member.rowspan
        )
