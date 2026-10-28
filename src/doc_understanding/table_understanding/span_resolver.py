"""Span is an interval length, and is null unless the physical enclosure is resolved."""


class MergedCellSpanResolver:
    def resolve(self, region):
        if not region.resolved:
            return None, None
        return region.row_end - region.row_start, region.column_end - region.column_start
