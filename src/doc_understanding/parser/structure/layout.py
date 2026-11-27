# 역할: 표 배치 명세(분석기 입력). 머리글 행 수와 행별 라벨 깊이로 역할 경계를 정한다. 원본 셀은 바꾸지 않는다.
"""표 하나에 '머리글 n행·라벨 m열' 하나만 두지 않고 행마다 라벨 깊이를 둔다.

구조 계약(models.py, 동결)은 바꾸지 않는다. 적용한 배치는 분석 결과의 LAYOUT_HYPOTHESIS 모호 기록에 남는다.
배치는 후보이며 사람 검증이 아니다.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class TableLayout:
    """header_rows: 위에서부터 머리글인 행 수(0이면 머리글 없는 표).
    row_stub_depths: 표의 행마다 왼쪽에서 라벨인 열 수(머리글 행의 값은 쓰지 않는다. 0이면 그 행에 라벨이 없다).
    header_spans_stub: 라벨 열과 값 열에 걸친 머리글 칸을 열 머리글로 본다(False면 예전처럼 역할 미정)."""
    header_rows: int
    row_stub_depths: tuple
    header_spans_stub: bool = True

    @classmethod
    def uniform(cls, rows, header_rows, stub_columns, header_spans_stub=True):
        return cls(header_rows, (stub_columns,) * rows, header_spans_stub)

    def depths(self, row_range):
        return self.row_stub_depths[row_range[0]:row_range[1]]

    def body_depths(self):
        return self.row_stub_depths[self.header_rows:]


def derive_layout(table, header_rows, stub_columns):
    """'머리글 n행·라벨 m열' 추정에서, 라벨 경계에 걸친 칸을 근거로 행별 라벨 깊이를 정한다(점유 범위만 본다).

    본문 칸이 라벨 열 경계(m)를 가로지를 때:
    - 표의 오른쪽 끝까지 닿으면 그 행의 값이다 → 그 행의 라벨 깊이는 칸의 시작 열.
      (예: '취급대상 | ▪ 내용(2열 병합)' — 다른 행에만 2단 라벨이 있는 표)
    - 오른쪽에 다른 칸이 남으면 여러 열에 걸친 라벨이다 → 그 행의 라벨 깊이는 칸의 끝 열.
      (예: '변동 금리형 | 1년(3개월 회전)(2열 병합) | 0.95')
    머리글 칸이 경계를 가로지르면 열 머리글로 본다(TableLayout.header_spans_stub).
    가로지르는 칸이 없으면 None(예전 추정으로 충분). 한 행에서 두 해석이 부딪히면 None(판단하지 않음).
    """
    from doc_understanding.parser.structure.trees import ranges
    rows, columns = len(table.rows), len(table.columns)
    if not 0 <= header_rows < rows or not 0 < stub_columns < columns:
        return None
    low, high, crossing_header = {}, {}, False
    for cell in table.cells:
        span = ranges(cell)
        if not span:
            continue
        rr, cc = span
        if not (cc[0] < stub_columns < cc[1]) or rr[1] > rows or cc[1] > columns:
            continue
        if rr[1] <= header_rows:
            crossing_header = True
        elif rr[0] >= header_rows:
            target = low if cc[1] >= columns else high
            for r in range(*rr):
                target[r] = min(target.get(r, cc[0]), cc[0]) if target is low else max(target.get(r, cc[1]), cc[1])
    if set(low) & set(high) or not (low or high or crossing_header):
        return None
    depths = tuple(low.get(r, high.get(r, stub_columns)) for r in range(rows))
    return TableLayout(header_rows, depths)
