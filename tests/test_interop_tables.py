"""바깥 표(HTML·마크다운)를 칸 목록으로 풀고 우리 격자와 짝짓는 변환."""
from doc_understanding.interop.tables import (
    align, align_by_position, compare, html_tables, layout, markdown_tables, otsl_table, shape)

HTML = """
<table>
 <thead>
  <tr><th rowspan="2">구분</th><th colspan="2">2024년(A)</th><th colspan="2">2025년(B)</th></tr>
  <tr><th>금액</th><th>구성비</th><th>금액</th><th>구성비</th></tr>
 </thead>
 <tr><th>투자자산</th><td>4,612</td><td>2.58</td><td>4,899</td><td>2.77</td></tr>
 <tr><th>유형자산</th><td>1,200</td><td>0.67</td><td>1,180</td><td>0.66</td></tr>
 <tr><th>기타</th><td>△280</td><td>0.30</td><td>0.30</td><td></td></tr>
</table>"""


def cell(row, col, text, rowspan=1, colspan=1):
    return dict(row=row, col=col, rowspan=rowspan, colspan=colspan, text=text)


OURS = [cell(0, 0, "구 분", rowspan=2), cell(0, 1, "2024년(A)", colspan=2), cell(0, 3, "2025년(B)", colspan=2),
        cell(1, 1, "금액"), cell(1, 2, "구성비"), cell(1, 3, "금액"), cell(1, 4, "구성비"),
        cell(2, 0, "투자자산"), cell(2, 1, "4,612"), cell(2, 2, "2.58"), cell(2, 3, "4,899"), cell(2, 4, "2.77"),
        cell(3, 0, "유형자산"), cell(3, 1, "1,200"), cell(3, 2, "0.67"), cell(3, 3, "1,180"), cell(3, 4, "0.66"),
        cell(4, 0, "기타"), cell(4, 1, "△280"), cell(4, 2, "0.30"), cell(4, 3, "0.30"), cell(4, 4, "")]


def test_html_spans_are_unfolded_into_grid_positions():
    table = html_tables(HTML)[0]
    assert shape(table) == (5, 5)
    at = {(c["row"], c["col"]): c for c in table}
    assert at[(0, 0)]["rowspan"] == 2 and at[(0, 1)]["colspan"] == 2 and at[(0, 3)]["text"] == "2025년(B)"
    # 위 칸이 세로로 차지한 자리를 건너뛰어, 둘째 줄의 첫 칸은 1열에서 시작한다.
    assert at[(1, 1)]["text"] == "금액" and (1, 0) not in at
    assert at[(4, 1)]["text"] == "△280" and at[(4, 4)]["text"] == ""
    assert [c["header"] for c in table if c["row"] == 2] == [True, False, False, False, False]


def test_layout_reads_header_rows_and_label_columns_the_table_marks():
    assert layout(html_tables(HTML)[0]) == dict(rows=5, columns=5, header_rows=2, label_columns=1, marks_headers=True)
    plain = html_tables("<table><tr><td>a</td><td>b</td></tr><tr><td>1</td><td>2</td></tr></table>")[0]
    assert layout(plain)["header_rows"] == 0 and layout(plain)["marks_headers"] is False


def test_cells_pair_up_even_when_the_same_number_repeats():
    theirs = html_tables(HTML)[0]
    pairs = align(OURS, theirs)
    assert len(pairs) == len(OURS)
    assert all(OURS[a]["row"] == theirs[b]["row"] and OURS[a]["col"] == theirs[b]["col"] for a, b in pairs.items())
    score = compare(OURS, theirs, pairs)
    # "구 분"과 "구분"은 공백만 다르다. 빈칸도 빈칸끼리 같다.
    assert score["matched"] == score["text_equal"] == score["span_equal"] == 22 and score["nonempty"] == 21


def test_a_row_the_other_table_dropped_is_left_unpaired_and_the_rest_still_line_up():
    theirs = html_tables(HTML.replace("<tr><th>유형자산</th><td>1,200</td><td>0.67</td><td>1,180</td><td>0.66</td></tr>", ""))[0]
    pairs = align(OURS, theirs)
    missing = [OURS[n]["text"] for n in range(len(OURS)) if n not in pairs]
    assert missing == ["유형자산", "1,200", "0.67", "1,180", "0.66"]
    last = next(n for n, c in enumerate(OURS) if c["text"] == "△280")
    assert theirs[pairs[last]]["text"] == "△280" and theirs[pairs[last]]["row"] == 3
    score = compare(OURS, theirs, pairs)
    assert score["cells"] == 22 and score["matched"] == 17 and score["text_equal"] == 17


def test_misread_text_still_pairs_and_counts_as_a_text_error():
    theirs = html_tables(HTML.replace("△280", "280").replace("4,612", "4,6l2"))[0]
    pairs = align(OURS, theirs)
    score = compare(OURS, theirs, pairs)
    assert score["matched"] == 22 and score["text_equal"] == 20


def test_markdown_tables_have_no_spans_and_mark_the_first_row_as_header():
    table = markdown_tables("앞 문장\n\n| 구분 | 금액 | 구성비 |\n|---|---:|---:|\n| 투자자산 | 4,612 | 2.58 |\n| 기타 | △280 |  |\n\n뒤 문장")[0]
    assert shape(table) == (3, 3) and all(c["rowspan"] == c["colspan"] == 1 for c in table)
    assert layout(table)["header_rows"] == 1 and table[-1]["text"] == ""
    assert markdown_tables("| 표가 아님 |\n그냥 줄") == []


def test_pairing_by_position_uses_the_largest_overlap():
    ours = [dict(bbox=(0, 0, 10, 10)), dict(bbox=(10, 0, 20, 10)), dict(bbox=(50, 50, 60, 60))]
    theirs = [dict(bbox=(9, 0, 21, 10)), dict(bbox=(0, 1, 9, 9))]
    assert align_by_position(ours, theirs) == {0: 1, 1: 0}  # 겹치지 않는 칸은 짝이 없다


def test_otsl_notation_is_unfolded_like_an_html_table():
    text = ("<fcel>구분<fcel>2024년(A)<lcel><nl><ucel><fcel>금액<fcel>구성비<nl>"
            "<fcel>투자자산<fcel>4,612<fcel>2.58<nl><fcel>기타<fcel>△280<ecel><nl>")
    cells = otsl_table(text)
    at = {(c["row"], c["col"]): c for c in cells}
    assert shape(cells) == (4, 3) and len(cells) == 10
    assert at[(0, 0)]["rowspan"] == 2 and at[(0, 1)]["colspan"] == 2 and (1, 0) not in at and (0, 2) not in at
    assert at[(3, 1)]["text"] == "△280" and at[(3, 2)]["text"] == ""
    assert layout(cells)["marks_headers"] is False and otsl_table("표가 아님") == []
    marked = otsl_table("<ched>구분<ched>금액<nl><rhed>기타<fcel>1<nl>")
    assert layout(marked) == dict(rows=2, columns=2, header_rows=1, label_columns=1, marks_headers=True)
