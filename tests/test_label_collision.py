"""라벨 글자 경로 충돌: 경로가 칸 하나를 가리키지 못하는 값 칸은 값 조회 근거에서 뺀다. 모양이 같아도 경로가
칸을 하나로 정하면 그대로 둔다(반례). 표는 테스트용으로 만든 것이며 특정 문서·기관과 무관하다."""
from doc_understanding.parser.assembly.document import build_from_run
from doc_understanding.parser.extraction.html_adapter import prepare_html
from doc_understanding.rag.evidence.cells import cells_for_table
from doc_understanding.rag.evidence.inspect import table_view


def document(tmp_path, rows):
    head, *body = rows
    html = "<table><tr>" + "".join(f"<th>{x}</th>" for x in head) + "</tr>" + "".join(
        "<tr>" + "".join(f"<td>{x}</td>" for x in row) + "</tr>" for row in body) + "</table>"
    source = tmp_path / "table.html"
    source.write_text(html, encoding="utf8")
    run, _ = prepare_html(source, tmp_path / "runs")
    return build_from_run(run)


def values(doc):
    return cells_for_table(doc, doc.table_structure[0].table_id)


def test_values_in_different_rows_with_the_same_label_path_are_withheld(tmp_path):
    # 첫 열만 행 라벨로 보면 같은 나이가 두 행에 있어, '15 to 19'의 Number가 어느 칸인지 정할 수 없다.
    doc = document(tmp_path, [["Age", "Household size", "Number"],
                              ["15 to 19", "Two people", "20"], ["15 to 19", "Three or more", "30"],
                              ["65 or over", "Two people", "70"]])
    found = values(doc)
    repeated = [v for v in found if v["row_path"] == ["15 to 19"]]
    assert sorted(v["value_raw"] for v in repeated) == ["20", "30", "Three or more", "Two people"]
    assert all(not v["lookup_eligible"] and v["path_collision"] == "label_text" for v in repeated)
    # 같은 표에서도 경로가 칸 하나를 가리키는 행은 그대로 쓴다.
    single = [v for v in found if v["row_path"] == ["65 or over"]]
    assert len(single) == 2 and all(v["lookup_eligible"] and "path_collision" not in v for v in single)
    view = table_view(doc, doc.table_structure[0].table_id)
    blocked = {c["text"]: c["blockers"] for c in view["cells"] if c["role"] == "value" and not c["eligible"]}
    assert blocked["20"] == ["PATH_COLLISION"] and "70" not in blocked


def test_same_shape_is_kept_when_the_first_column_identifies_each_row(tmp_path):
    # 반례: 열 구성은 같지만 첫 열이 행마다 달라 경로가 칸을 하나로 정한다. 둘째 열은 그 행의 속성(값)이다.
    doc = document(tmp_path, [["Name", "Department", "Salary"],
                              ["Kim", "Sales", "100"], ["Lee", "Sales", "100"], ["Park", "Audit", "90"]])
    found = values(doc)
    assert len(found) == 6 and all(v["lookup_eligible"] and "path_collision" not in v for v in found)


def test_repeated_values_alone_are_not_a_collision(tmp_path):
    # 반례: 값이 같아도(100, 100) 라벨 경로가 다르면 충돌이 아니다. 값은 비교하지 않는다.
    doc = document(tmp_path, [["Item", "Amount"], ["A", "100"], ["B", "100"]])
    assert all(v["lookup_eligible"] for v in values(doc))
