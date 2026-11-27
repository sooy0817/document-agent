"""원문 격자 읽기: 구조를 확정하지 못한 표의 칸을 모델에 읽히고, 고른 칸을 원문과 대조한다."""
import json

import pytest

from doc_understanding.rag.agentic import grid
from doc_understanding.rag.agentic.grid import GridAnswer, check, header_source, read_tables, sparse
from doc_understanding.runtime.structured_call import CallPolicy

POLICY = CallPolicy(model="small", input_usd_per_million=1, output_usd_per_million=1)


def cell(r, c, text, rs=1, cs=1):
    return dict(id=f"cell-r{r}c{c}", r=r, c=c, rs=rs, cs=cs, text=text, bbox=dict(x0=c, y0=r, x1=c + 1, y1=r + 1))


def table(rows, table_id="t1", page=3):
    return dict(table_id=table_id, page=page, cells=[cell(r, c, text) for r, row in enumerate(rows) for c, text in enumerate(row)])


# 좌우로 나란한 두 묶음: 왼쪽(계정, 당기) | 오른쪽(계정, 당기)
SIDE = table([["계정과목", "당기", "계정과목", "당기"], ["현금", "1,138", "외상매입금", "185"], ["재고자산", "1,392", "단기차입금", "6,686"]])
GRIDS = [(0, SIDE)]


def reply(**fields):
    base = dict(kind="answered", answer_cells=[], row_label_cells=[], column_label_cells=[], answer_text="", explanation="")
    return GridAnswer.model_validate({**base, **fields})


def transport_for(answer):
    def transport(payload, key, timeout):
        transport.user = json.loads(payload["input"][1]["content"])
        return dict(status="completed", model="small-1", usage=dict(input_tokens=1000, output_tokens=100),
                    output=[dict(type="message", content=[dict(type="output_text", text=json.dumps(answer.model_dump()))])])
    return transport


def test_an_answer_with_aligned_labels_and_a_copied_value_passes():
    good = reply(answer_cells=["T0:r2c3"], row_label_cells=["T0:r2c2"], column_label_cells=["T0:r0c3"], answer_text="단기차입금은 6,686입니다.")
    result = check(good, GRIDS, {}, "당기 단기차입금은?")
    assert result["passed"] and [a[2]["text"] for a in result["answers"]] == ["6,686"]


def test_named_row_must_be_unique_and_withheld_in_the_document():
    question = "두 기관의 평균처리시간은 각각 얼마인가요?"
    a = dict(rows=["평 균 처 리 시 간"], eligible=0)
    b = dict(rows=["평균대기시간"], eligible=3)
    assert grid.withheld_row([a, b], question) == (a, "평균처리시간")
    assert grid.withheld_row([a, dict(a, eligible=3)], question) is None
    assert grid.withheld_row([dict(a, eligible=3)], question) is None


def test_row_discovery_uses_only_complete_labels_and_question_grounded_query_terms():
    outline = dict(rows=["재 작 업 수 량 비 율"], eligible=0)
    question = "수량 중 재작업된 제품이 차지한 비율은?"
    assert grid.withheld_row([outline], question, ["수량 재작업 비율"]) == (outline, "재작업수량비율")
    assert grid.withheld_row([outline], "전체 수량의 비율은?", ["수량 재작업 비율"]) is None
    other = dict(rows=["수 량 재 작 업 비 율"], eligible=2)
    assert grid.withheld_row([outline, other], question, ["수량 재작업 비율"]) is None


def test_joined_subjects_do_not_excuse_a_missing_metric():
    assert grid.untouched(["서부센터와 동부센터", "처리시간"], ["처리시간"], "서부센터 동부센터") == []
    assert grid.untouched(["서부센터와 동부센터 처리시간"], ["대기시간"], "서부센터 동부센터") == ["서부센터와 동부센터 처리시간"]


def test_value_period_is_checked_even_when_it_also_appears_in_the_document_name():
    from doc_understanding.rag.agentic.plan import Requirement

    condition = Requirement(role="condition", text="2031년 조건", question_quote="2031년에")
    answer = reply(answer_cells=["T0:r1c1"], row_label_cells=["T0:r1c0"], column_label_cells=["T0:r0c1"], answer_text="14")
    for header, valid in [("2031년", True), ("2030년", False)]:
        sheet = table([["기간", header], ["검사시간", "14"]])
        assert check(answer, [(0, sheet)], {}, "2031년에 검사시간은?", exempt="센터 2031년",
                     planned_requirements=[condition])["passed"] is valid
    assert grid.requested_years("2030~2031년의 검사시간", [], []) == {"2030", "2031"}


@pytest.mark.parametrize("second_header,expected", [("2031", "answered"), ("2029년", "rejected"),
                                                  ("2031원", "rejected"), ("2030년 / 2031년", "rejected")])
def test_comparison_binds_year_value_and_unit_to_each_document(tmp_path, monkeypatch, second_header, expected):
    from doc_understanding.rag.agentic.plan import Requirement

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    grids = {tid: dict(table([["항목", header], ["처리율", value]], tid), unit_context=[dict(text_raw="단위: %")])
             for tid, header, value in [("a", "2031년", "11"), ("b", second_header, "22")]}
    scope = Requirement(role="scope", question_quote="2032년 운영 보고서", text="2032년 운영 보고서")
    swapped = reply(answer_cells=["T0:r1c1", "T1:r1c1"], row_label_cells=["T0:r1c0", "T1:r1c0"],
                    column_label_cells=["T0:r0c1", "T1:r0c1"], answer_text="서부 22%, 동부 11%")
    names = {"a": "서부 2032년 운영 보고서", "b": "동부 2032년 운영 보고서"}
    result, evidence, _ = read_tables("2032년 운영 보고서에서 2031년 처리율은?", [("a", "a"), ("b", "b")],
                                      lambda rid, tid: grids[tid], None, POLICY, tmp_path / "grid", allow_external=True,
                                      transport=transport_for(swapped), describe_document=names.get, requirements=[scope],
                                      required_rows={"a": "처리율", "b": "처리율"})
    assert result["status"] == expected
    if expected == "answered":
        west, east = result["answer_text"].splitlines()
        assert "서부" in west and west.endswith("11%") and "동부" in east and east.endswith("22%")
        assert [e["run_id"] for e in evidence] == ["a", "b"]
    else:
        assert result["reasons"] == ["REQUESTED_PERIOD_NOT_CITED"] and not evidence


@pytest.mark.parametrize("unit,supported", [("(단위 : %)", True), ("(단위 : 백만원, %)", False), ("원", False)])
def test_percent_must_come_from_the_answer_cells_own_table(unit, supported):
    value = reply(answer_cells=["T0:r1c1"], row_label_cells=["T0:r1c0"], column_label_cells=["T0:r0c1"],
                  answer_text="처리율은 71.20%입니다.")
    t = table([["항목", "당기"], ["처리율", "71.20"]])
    other = dict(t, unit_context=[dict(text_raw="단위: %")])
    verdict = check(value, [(0, dict(t, unit_context=[dict(text_raw=unit)])), (1, other)], {}, "당기 처리율은?")
    assert verdict["passed"] is supported


def test_named_row_grid_rejects_correctly_aligned_but_unrequested_row(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    other_row = reply(answer_cells=["T0:r1c3"], row_label_cells=["T0:r1c2"], column_label_cells=["T0:r0c3"],
                      answer_text="185입니다.")
    verdict, evidence, _ = read_tables("당기 단기차입금은?", [("run-1", "t1")], lambda run, tid: SIDE, None,
                                       POLICY, tmp_path / "grid", allow_external=True, transport=transport_for(other_row),
                                       required_rows={"t1": "단기차입금"})
    assert verdict["reasons"] == ["REQUESTED_ROW_OR_COLUMN_NOT_CITED"]
    assert evidence == []


@pytest.mark.parametrize("fields,reason", [
    (dict(answer_cells=["T0:r9c9"], row_label_cells=["T0:r2c2"]), "UNKNOWN_CELL_REFERENCE"),
    (dict(answer_cells=["T0:r2c3"], answer_text="6,686"), "NO_LABEL_CELL"),                                # 무엇의 값인지 가리키는 칸이 없다
    (dict(answer_cells=["T0:r2c3"], row_label_cells=["T0:r1c2"], answer_text="6,686"), "LABEL_NOT_ALIGNED"),   # 다른 행의 라벨
    (dict(answer_cells=["T0:r2c3"], column_label_cells=["T0:r0c1"], answer_text="6,686"), "LABEL_NOT_ALIGNED"),  # 다른 열의 머리글
    (dict(answer_cells=["T0:r2c3"], row_label_cells=["T0:r2c2"], answer_text="6,868입니다"), "UNSUPPORTED_NUMBER_IN_TEXT"),
    (dict(answer_cells=["T0:r2c3"], row_label_cells=["T0:r2c2"], answer_text=""), "EMPTY_ANSWER_TEXT"),
    # 질문에 나온 숫자를 되풀이했을 뿐 인용한 칸의 값을 말하지 않은 답
    (dict(answer_cells=["T0:r2c3"], row_label_cells=["T0:r2c2"], answer_text="단기차입금은 2025입니다."), "CITED_VALUE_NOT_IN_ANSWER_TEXT"),
    (dict(answer_cells=["T0:r2c3"], row_label_cells=["T0:r2c3"], answer_text="6,686"), "NO_LABEL_CELL"),     # 답 칸을 라벨로 댄 것은 라벨이 아니다
])
def test_answers_that_do_not_hold_up_against_the_grid_are_rejected(fields, reason):
    assert reason in check(reply(**fields), GRIDS, {}, "2025년 당기 단기차입금은?")["reasons"]


def test_sparse_grids_are_not_tables():
    assert sparse(table([["", "", "1.0"], ["", "", "0.8"], ["", "", ""]])) and sparse(dict(cells=[])) and not sparse(SIDE)


def test_header_source_follows_same_width_tables_back_through_consecutive_pages():
    shapes = [("p1", 1, 9), ("p2", 2, 9), ("p3", 3, 9), ("other", 3, 4), ("p5", 5, 9)]
    assert header_source(shapes, "p3") == "p1" and header_source(shapes, "p2") == "p1"
    assert header_source(shapes, "p1") is None            # 맨 앞 표는 이어진 것이 아니다
    assert header_source(shapes, "p5") is None            # 4쪽에 같은 폭의 표가 없으면 이어진 것으로 보지 않는다
    assert header_source(shapes, "other") is None and header_source(shapes, "없는 표") is None


def test_read_tables_sends_raw_cells_and_returns_cited_cells_as_unconfirmed_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    answer = reply(answer_cells=["T0:r2c3"], row_label_cells=["T0:r2c2"], column_label_cells=["T0:r0c3"],
                   answer_text="단기차입금은 6,686입니다.")
    transport = transport_for(answer)
    verdict, evidence, record = read_tables("당기 단기차입금은?", [("run-1", "t1")], lambda run, tid: SIDE, lambda run: [("t1", 3, 4)],
                                            POLICY, tmp_path / "grid", allow_external=True, transport=transport,
                                            describe_document=lambda run: "가람농협 | 재무상태표")
    assert verdict == dict(status="answered", answer_text="단기차입금은 6,686입니다.", explanation="")
    # 모델에는 칸 위치와 글자만 준다(머리글·행 라벨이 어디인지는 주지 않는다).
    assert 'T0:r2c3 | "6,686"' in transport.user["cells"] and transport.user["tables"] == [dict(table="T0", document="가람농협 | 재무상태표", page=3)]
    item = evidence[0]
    assert item["cell_id"] == "cell-r2c3" and item["value_raw"] == "6,686" and item["run_id"] == "run-1" and item["page"] == 3
    assert item["row_path"] == ["단기차입금"] and item["column_path"] == ["당기"]
    # 구조 해석이 확정한 경로가 아니다: 조회 가능으로 두지 않고, 원문 위치는 그대로 싣는다.
    assert item["lookup_eligible"] is False and item["structure"] == "raw_grid_reading" and item["source"]["page_number"] == 3
    assert record["tables"] == [dict(run_id="run-1", table_id="t1", read=True, page=3, cells=12, header_candidate_from=None,
                                     relevance=record["tables"][0]["relevance"])]
    assert record["check"] == dict(passed=True, reasons=[])


def test_continued_table_gets_the_earlier_first_row_as_a_header_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    first = table([["순번", "보증금", "월세"], ["1", "5,000", "550"]], "p1", page=1)
    second = table([["68", "1,000", "390"], ["69", "157,500", ""]], "p2", page=2)
    answer = reply(answer_cells=["T0:r0c2"], row_label_cells=["T0:r0c0"], column_label_cells=["H0:c2"], answer_text="순번 68의 월세는 390입니다.")
    transport = transport_for(answer)
    verdict, evidence, record = read_tables("순번 68의 월세는?", [("run", "p2")], lambda run, tid: dict(p1=first, p2=second)[tid],
                                            lambda run: [("p1", 1, 3), ("p2", 2, 3)], POLICY, tmp_path / "grid",
                                            allow_external=True, transport=transport)
    assert 'H0:c2 | "월세"' in transport.user["cells"] and record["tables"][0]["header_candidate_from"] == "p1"
    assert verdict["status"] == "answered" and evidence[0]["column_path"] == ["월세"] and evidence[0]["row_path"] == ["68"]
    # 다른 열의 머리글 후보를 대면 버린다.
    wrong = reply(answer_cells=["T0:r0c2"], row_label_cells=["T0:r0c0"], column_label_cells=["H0:c1"], answer_text="390")
    verdict, evidence, _ = read_tables("순번 68의 월세는?", [("run", "p2")], lambda run, tid: dict(p1=first, p2=second)[tid],
                                       lambda run: [("p1", 1, 3), ("p2", 2, 3)], POLICY, tmp_path / "grid2",
                                       allow_external=True, transport=transport_for(wrong))
    assert verdict == dict(status="rejected", reasons=["LABEL_NOT_ALIGNED"]) and evidence == []


def test_nothing_is_sent_for_charts_oversized_tables_or_missing_tables(tmp_path):
    chart = table([["", "", "1.0"], ["", "", "0.8"], ["", "", ""]], "chart")
    big = table([["가" * 7000, "나" * 7000]], "big")

    def never(*args):
        raise AssertionError("no model call")

    verdict, evidence, record = read_tables("질문", [("r", "chart"), ("r", "big"), ("r", "gone")],
                                            lambda run, tid: dict(chart=chart, big=big).get(tid), None, POLICY, tmp_path / "grid",
                                            allow_external=True, transport=never)
    assert verdict == dict(status="nothing_to_read") and evidence == []
    assert sorted(t["reason"] for t in record["tables"]) == ["NOT_FOUND", "SPARSE_GRID", "TOO_LARGE"]
    assert not (tmp_path / "grid").exists()


def test_model_saying_it_is_not_a_table_is_not_an_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    verdict, evidence, record = read_tables("질문", [("r", "t1")], lambda run, tid: SIDE, None, POLICY, tmp_path / "grid",
                                            allow_external=True, transport=transport_for(reply(kind="not_in_grid")))
    assert verdict["status"] == "not_answered" and verdict["kind"] == "not_in_grid" and evidence == []
    assert grid.MAX_TABLES == 2 and set(GridAnswer.model_json_schema()["required"]) == set(GridAnswer.model_json_schema()["properties"])


def test_a_merged_cell_can_be_referenced_by_any_position_it_covers():
    merged = dict(table_id="m", page=1, cells=[cell(0, 0, "구분"), cell(0, 1, "금액"), cell(1, 0, "가람지구", rs=2), cell(1, 1, "100"), cell(2, 1, "200")])
    answer = reply(answer_cells=["T0:r2c1"], row_label_cells=["T0:r2c0"], column_label_cells=["T0:r0c1"], answer_text="200")
    result = check(answer, [(0, merged)], {}, "가람지구 둘째 줄 금액은?")
    assert result["passed"] and result["row_labels"][0][2]["text"] == "가람지구"


def test_a_numeric_label_must_be_a_number_the_question_asked_for():
    listing = table([["291", "주소 A", "3,000"], ["76", "주소 B", "20,000"]])
    wrong = reply(answer_cells=["T0:r0c2"], row_label_cells=["T0:r0c0"], answer_text="3,000")
    assert check(wrong, [(0, listing)], {}, "순번 76번의 보증금은?")["reasons"] == ["NUMERIC_LABEL_NOT_IN_QUESTION"]
    right = reply(answer_cells=["T0:r1c2"], row_label_cells=["T0:r1c0"], answer_text="20,000")
    assert check(right, [(0, listing)], {}, "순번 76번의 보증금은?")["passed"]
    # 104를 물었는데 1040번 행을 고른 것도 걸러진다.
    near = table([["1040", "주소", "5,000"]])
    assert not check(reply(answer_cells=["T0:r0c2"], row_label_cells=["T0:r0c0"], answer_text="5,000"), [(0, near)], {}, "순번 104번은?")["passed"]


def test_the_table_that_matches_the_question_best_is_read_first(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(grid, "MAX_TABLES", 1)
    other = table([["290", "주소 A", "1,000"], ["291", "주소 B", "3,000"]], "other", page=5)
    wanted = table([["75", "주소 C", "5,000"], ["76", "주소 D", "20,000"]], "wanted", page=2)
    answer = reply(answer_cells=["T0:r1c2"], row_label_cells=["T0:r1c0"], answer_text="20,000")
    transport = transport_for(answer)
    # 검색 순위는 other가 앞이지만, 질문의 '76'과 글자가 똑같은 칸이 있는 표를 먼저 읽힌다.
    verdict, evidence, record = read_tables("순번 76번 주택의 보증금은?", [("r", "other"), ("r", "wanted")],
                                            lambda run, tid: dict(other=other, wanted=wanted)[tid], None, POLICY, tmp_path / "grid",
                                            allow_external=True, transport=transport, terms=["순번 76번", "보증금"],
                                            check_requirements=False)  # 이 시험은 읽을 표의 순서만 본다
    assert verdict["status"] == "answered" and evidence[0]["table_id"] == "wanted" and evidence[0]["value_raw"] == "20,000"
    assert {t["table_id"]: t.get("reason") for t in record["tables"]} == dict(wanted=None, other="LOWER_RELEVANCE")


def test_a_cell_holding_a_header_line_and_a_value_line_is_cited_by_its_value_line():
    mixed = dict(table_id="x", page=1, cells=[cell(0, 0, "상품명\n가람보험"), cell(0, 1, "신용등급\nAAA")])
    answer = reply(answer_cells=["T0:r0c1"], row_label_cells=["T0:r0c0"], answer_text="신용등급은 AAA입니다.")
    assert check(answer, [(0, mixed)], {}, "가람보험의 신용등급은?")["passed"]


def test_each_requirement_of_the_question_must_touch_a_cited_label():
    estates = table([["단지명", "유형", "월임대료"], ["가람마을", "29", "207,000"], ["나래마을", "29", "241,000"]])
    wrong = reply(answer_cells=["T0:r2c2"], row_label_cells=["T0:r2c0", "T0:r2c1"], column_label_cells=["T0:r0c2"], answer_text="241,000")
    right = reply(answer_cells=["T0:r1c2"], row_label_cells=["T0:r1c0", "T0:r1c1"], column_label_cells=["T0:r0c2"], answer_text="207,000")
    requirements = ["다온시 입주자 모집공고", "가람마을", "29형", "월임대료"]
    exempt = "다온시 입주자 모집공고"
    # 다른 단지의 같은 유형 행을 고르면 질문이 말한 단지 이름이 라벨에 없다.
    assert check(wrong, [(0, estates)], {}, "가람마을 29형 월임대료는?", requirements, exempt)["reasons"] == ["REQUIREMENT_NOT_IN_LABELS"]
    assert check(right, [(0, estates)], {}, "가람마을 29형 월임대료는?", requirements, exempt)["passed"]
    # 문서 이름을 말한 요구 사항은 표의 라벨로 확인하지 않는다. 면제하지 않으면 걸린다.
    assert not check(right, [(0, estates)], {}, "가람마을 29형 월임대료는?", requirements, "")["passed"]
    # 요구 사항을 주지 않으면(계획이 없으면) 이 검사는 하지 않는다.
    assert check(wrong, [(0, estates)], {}, "가람마을 29형 월임대료는?")["passed"]


def test_every_answer_cell_needs_its_own_aligned_label():
    two = reply(answer_cells=["T0:r2c3", "T0:r1c1"], row_label_cells=["T0:r2c2"], answer_text="6,686과 1,138입니다.")
    assert "NO_LABEL_CELL" in check(two, GRIDS, {}, "단기차입금과 현금은?")["reasons"]
    both = reply(answer_cells=["T0:r2c3", "T0:r1c1"], row_label_cells=["T0:r2c2", "T0:r1c0"], answer_text="6,686과 1,138입니다.")
    assert check(both, GRIDS, {}, "단기차입금과 현금은?")["passed"]
