# 역할: 확인된 물리 경계로 행·열 병합 길이를 계산하고 불확실한 길이는 미확정으로 둔다.
"""Span is an interval length, and is null unless the physical enclosure is resolved."""


# 복원된 영역의 물리 행·열 점유 길이를 병합 span으로 변환한다.
class MergedCellSpanResolver:
    # 영역이 resolved일 때만 rowspan/colspan을 반환하고 아니면 둘 다 None으로 남긴다.
    def resolve(self, region):
        if not region.resolved:
            return None, None
        return region.row_end - region.row_start, region.column_end - region.column_start
