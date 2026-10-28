"""Geometric occupancy only; no parent/category inference."""


class CellToRowAlignment:
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

    @staticmethod
    def row_range_contains(group, member):
        """Compare occupied row intervals. Does not assert a semantic parent relation."""
        if group.status != "resolved" or member.status != "resolved":
            return None
        return (
            group.row_index <= member.row_index
            and group.row_index + group.rowspan >= member.row_index + member.rowspan
        )
