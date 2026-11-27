"""확정으로 통과했지만 근거가 한쪽뿐인 경로를 값 조회 근거에서 보류한다. 표는 테스트용으로 만든 것이다."""
from types import SimpleNamespace as NS

from doc_understanding.rag.evidence.cells import header_from_corner_only, reversed_marker_links


def cell(row, column, rowspan=1, colspan=1):
    return NS(occupied_slots=[(r, c) for r in range(row, row + rowspan) for c in range(column, column + colspan)])


def table(cells, header_rows, panels=1):
    return NS(cells=cells), NS(panels=[NS(header_rows=header_rows)] * panels)


def test_a_merged_row_label_at_the_top_left_is_not_proof_of_header_rows():
    # 머리글 없이 이어진 표: 왼쪽 위 칸은 세 줄에 걸친 행 라벨이고, 그 옆 세 줄은 아래와 같은 모양의 데이터 행이다.
    body = [cell(0, 0, rowspan=3)] + [cell(r, c) for r in range(5) for c in range(1, 4)] + [cell(3, 0, rowspan=2)]
    assert header_from_corner_only(*table(body, header_rows=3))


def test_a_header_with_a_cell_spanning_the_columns_below_it_is_kept():
    # 실제 두 줄 머리글: '구분'이 두 줄에 걸치고, 위 줄의 칸이 아래 두 열을 가로로 묶는다.
    cells = [cell(0, 0, rowspan=2), cell(0, 1, colspan=2), cell(1, 1), cell(1, 2)] + [cell(2, c) for c in range(3)]
    assert not header_from_corner_only(*table(cells, header_rows=2))


def test_other_ways_of_finding_the_header_are_not_touched():
    flat = [cell(r, c) for r in range(3) for c in range(3)]
    assert not header_from_corner_only(*table(flat, header_rows=1))      # 한 줄 머리글
    tall = [cell(0, 0, rowspan=2)] + [cell(r, c) for r in range(4) for c in range(1, 3)] + [cell(2, 0), cell(3, 0)]
    assert not header_from_corner_only(*table(tall, header_rows=1))      # 머리글 행 수를 다른 근거로 정했다
    assert not header_from_corner_only(*table(tall, header_rows=2, panels=2))
    # 가로 병합이 머리글 구간 밖(본문)에만 있으면 머리글의 근거가 아니다.
    below = [cell(0, 0, rowspan=2)] + [cell(r, c) for r in range(2) for c in range(1, 3)] + [cell(2, 0), cell(2, 1, colspan=2)]
    assert header_from_corner_only(*table(below, header_rows=2))


def node(i, label, row, parent=None, rows=1):
    return NS(node_id=i, label_raw=label, row_range=(row, row + rows), parent_id=parent)


def test_a_parent_against_the_numbering_order_of_the_document_is_found():
    first = NS(row_tree=[node("a", "I. Assets", 0), node("b", "1. Cash", 1, "a"), node("c", "2. Deposits", 2, "a"),
                         node("d", "II. Loans", 3), node("e", "1. Short", 4, "d")])
    # 이어진 표: 하위 번호가 먼저 나와, 뒤에 나온 상위 번호가 그 아래로 붙었다.
    continued = NS(row_tree=[node("f", "4. Other", 0), node("g", "VIII. Sundry", 1, "f")])
    assert reversed_marker_links(NS(table_structure=[first, continued])) == {"g"}


def test_equal_counts_and_merged_labels_decide_nothing():
    even = NS(row_tree=[node("a", "I. Assets", 0), node("b", "1. Cash", 1, "a"), node("c", "2. Other", 2), node("d", "II. Loans", 3, "c")])
    assert reversed_marker_links(NS(table_structure=[even])) == frozenset()
    # 같은 행을 감싼 병합 라벨의 상하는 격자가 근거이므로 번호 순서로 뒤집지 않는다.
    merged = NS(row_tree=[node("a", "1. Group", 0, rows=2), node("b", "I. Item", 0, "a"), node("c", "II. Item", 1, "a"),
                          node("d", "III. Section", 2), node("e", "2. Line", 3, "d")])
    assert reversed_marker_links(NS(table_structure=[merged])) == frozenset()
