"""답변 표시: 짧은 답변 문장·셀 한 줄 인용·다른 해석. 모델 응답은 모의다."""
import json

import pytest

from doc_understanding.rag.generation.answer import (
    Alternative,
    PresentedSelection,
    check_selection,
    generate,
    present,
    template_text,
)

CELL = dict(evidence_id="E1", value_raw="(세금공제전,연%)\n예치기간 이자율\n15일 미만 0.10\n15일 이상~1개월 미만 0.30",
            row_path=["최초 만기 前", "회전기간이 지나지 않은 부분"], column_path=["일반중도해지 이자율"],
            lookup_eligible=True, table_id="t", cell_id="c", page=3, source_name="input.pdf")
ROW = dict(evidence_id="E2", value_raw="만기이자율 X 30%", row_path=["만기후 1개월초과~3개월이내"],
           column_path=["이자율"], lookup_eligible=True, table_id="t2", cell_id="c2", page=5, source_name="input.pdf")


def parsed(text, values, ids, alternatives=()):
    return PresentedSelection.model_validate(dict(
        evidence_ids=ids, values_raw=values, abstained=not ids, answer_text=text,
        alternatives=[dict(a) for a in alternatives],
        assessment=dict(question="q", status="supported", requires_external_verification=False,
                        requirements=[dict(question_quote="q", requirement="r", status="supported", supports=[])],
                        explanation="")))


def test_an_exact_contiguous_part_of_the_source_can_be_the_value():
    for part in ("15일 미만 0.10", "0.10", "15일 미만", "15일 미만 0.10 15일 이상~1개월 미만 0.30"):
        s = check_selection(dict(evidence_ids=["E1"], values_raw=[part], abstained=False), [CELL])
        assert s["values_raw"] == [part]


def test_a_part_that_cuts_a_number_or_is_not_in_the_source_is_rejected():
    long = dict(CELL, value_raw="Real GDP increased 2.2 percent in 2025, and 1,000 firms answered 1/10 of it.")
    for ok in ("2.2 percent", "1,000 firms", "1/10", "Real GDP increased 2.2 percent in 2025"):
        check_selection(dict(evidence_ids=["E1"], values_raw=[ok], abstained=False), [long])
    for bad in ("2.2 percent in 20", ".2 percent", "000 firms", "1/1", "eal GDP", "2.3 percent", "GDP rose 2.2", ""):
        with pytest.raises(ValueError, match="UNSUPPORTED_ANSWER_VALUE_OR_CITATION"):
            check_selection(dict(evidence_ids=["E1"], values_raw=[bad], abstained=False), [long])
    signed = dict(CELL, value_raw="당기순이익 -2.2 전년 △2,170 증감 (1,234) 비율 .10 변동 +3.5 기간 2024-2025 약 △ 55 "
                                  "차이 - 7.5 지수 1e10 분기 Q3 범위 2021 - 2022 연4.5%")
    for ok in ("-2.2", "△2,170", "(1,234)", ".10", "+3.5", "2024-2025", "△ 55", "당기순이익 -2.2", "- 7.5",
               "1e10", "Q3", "2021 - 2022", "2021", "4.5%"):
        check_selection(dict(evidence_ids=["E1"], values_raw=[ok], abstained=False), [signed])
    # 부호·앞 소수점·괄호 음수·지수·붙은 영문을 떼어 내면 다른 값이다.
    for bad in ("2.2", "2,170", "1,234", "10", "3.5", "55", "(1,234", "7.5", "1e", "3", "e10", "2025", "2022"):
        with pytest.raises(ValueError, match="UNSUPPORTED_ANSWER_VALUE_OR_CITATION"):
            check_selection(dict(evidence_ids=["E1"], values_raw=[bad], abstained=False), [signed])
    prose = dict(CELL, value_raw="Net income -2.2 and loss - 3.1 in COVID-19 from 2021 -5 is non-refundable\n2025\n- 4.4")
    for ok in ("-2.2", "- 3.1", "Net income -2.2", "-5", "COVID-19", "non-refundable", "- 4.4", "2025 - 4.4"):
        check_selection(dict(evidence_ids=["E1"], values_raw=[ok], abstained=False), [prose])
    for bad in ("2.2", "3.1", "5", "income 2.2", "19", "refundable", "4.4", "COVID"):
        with pytest.raises(ValueError, match="UNSUPPORTED_ANSWER_VALUE_OR_CITATION"):
            check_selection(dict(evidence_ids=["E1"], values_raw=[bad], abstained=False), [prose])
    # 낱말 앞부분을 떼면 뜻이 뒤집힐 수 있다(불가능 → 가능). 뒤에 붙은 조사는 뗄 수 있다.
    words = dict(CELL, value_raw="중도해지 불가능하며 미해당 상품은 1년 이내에 해지")
    for ok in ("불가능", "미해당 상품", "1년 이내", "중도해지 불가능하며"):
        check_selection(dict(evidence_ids=["E1"], values_raw=[ok], abstained=False), [words])
    for bad in ("가능", "해당 상품", "도해지", "가능하며"):
        with pytest.raises(ValueError, match="UNSUPPORTED_ANSWER_VALUE_OR_CITATION"):
            check_selection(dict(evidence_ids=["E1"], values_raw=[bad], abstained=False), [words])
    from doc_understanding.rag.generation.answer import render_answer
    part = dict(evidence_ids=["E1"], values_raw=["1년 이내"], abstained=False)
    assert "1년 이내 (원문 일부)" in render_answer(part, [words])
    whole = dict(evidence_ids=["E1"], values_raw=[words["value_raw"]], abstained=False)
    assert "(원문 일부)" not in render_answer(whole, [words])
    for bad in ("0.1", "5일 미만 0.10", "0.20"):
        with pytest.raises(ValueError, match="UNSUPPORTED_ANSWER_VALUE_OR_CITATION"):
            check_selection(dict(evidence_ids=["E1"], values_raw=[bad], abstained=False), [CELL])


def test_the_cells_own_label_can_be_the_value_and_is_shown_as_a_label():
    from doc_understanding.rag.generation.answer import from_label, render_answer, template_text

    rate = dict(evidence_id="E3", value_raw="0.95", row_path=["변동 금리형", "1년(3개월 회전)"],
                column_path=["이자율 (연%)"], lookup_eligible=True, table_id="t3", cell_id="c3", page=1,
                source_name="input.pdf")
    for label in ("1년(3개월 회전)", "3개월 회전", "변동 금리형"):
        sel = dict(evidence_ids=["E3"], values_raw=[label], abstained=False)
        assert check_selection(sel, [rate])["values_raw"] == [label] and from_label(label, rate)
    sel = dict(evidence_ids=["E3"], values_raw=["3개월 회전"], abstained=False)
    assert "3개월 회전 (이 칸의 행·열 라벨에서 인용. 칸 값: 0.95)" in render_answer(sel, [rate])
    assert template_text(sel, [rate]) == "3개월 회전 (input.pdf 1쪽)"
    assert not from_label("0.95", rate)
    # 그 칸의 라벨에 없는 글자, 숫자를 자른 값, 다른 칸의 라벨은 인정하지 않는다.
    for bad in ("6개월 회전", "0.9", "13개월", "만기후 1개월초과~3개월이내"):
        with pytest.raises(ValueError, match="UNSUPPORTED_ANSWER_VALUE_OR_CITATION"):
            check_selection(dict(evidence_ids=["E3"], values_raw=[bad], abstained=False), [rate])


def test_model_answer_text_is_kept_when_it_contains_selected_values_only():
    sel = dict(evidence_ids=["E2"], values_raw=["만기이자율 X 30%"], abstained=False)
    out = present(parsed("만기 후 2개월이면 만기이자율 X 30%가 적용됩니다.", sel["values_raw"], ["E2"]), sel, [ROW],
                  "만기 2개월 됐을때의 이자율은?")
    assert out["answer_text_source"] == "model" and "30%" in out["answer_text"]


@pytest.mark.parametrize("text,reason", [
    ("이자율은 만기이자율의 일부입니다.", "SELECTED_VALUE_NOT_IN_TEXT"),
    ("만기이자율 X 30%, 최대 5% 가산", "UNSUPPORTED_NUMBER_IN_TEXT"),
    ("", "EMPTY_ANSWER_TEXT"),
])
def test_invalid_answer_text_falls_back_to_template(text, reason):
    sel = dict(evidence_ids=["E2"], values_raw=["만기이자율 X 30%"], abstained=False)
    out = present(parsed(text, sel["values_raw"], ["E2"]), sel, [ROW])
    assert out["answer_text_source"] == "template" and reason in out["answer_text_reasons"]
    assert out["answer_text"] == "만기후 1개월초과~3개월이내 / 이자율: 만기이자율 X 30% (input.pdf 5쪽)"


def test_alternatives_keep_only_known_evidence_and_abstention_has_no_text():
    sel = dict(evidence_ids=["E2"], values_raw=["만기이자율 X 30%"], abstained=False)
    alts = [dict(interpretation="2개월 예치 후 중도해지", evidence_ids=["E1"]),
            dict(interpretation="없는 근거", evidence_ids=["E9"])]
    out = present(parsed("만기이자율 X 30%입니다.", sel["values_raw"], ["E2"], alts), sel, [ROW, CELL])
    assert out["alternatives"] == [dict(interpretation="2개월 예치 후 중도해지", evidence_ids=["E1"])]
    held = dict(evidence_ids=[], values_raw=[], abstained=True)
    out = present(parsed("아무 말", [], []), held, [ROW])
    assert out["answer_text"] == "" and out["answer_text_source"] == "none"
    assert template_text(held, [ROW]) == ""
    assert Alternative(interpretation="x", evidence_ids=[]).evidence_ids == []


def test_generate_requests_presentation_schema_and_returns_answer_text(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-never-sent")
    seen = {}
    draft = dict(evidence_ids=["E2"], values_raw=["만기이자율 X 30%"], abstained=False,
                 answer_text="만기 후 2개월이면 만기이자율 X 30%입니다.", alternatives=[],
                 assessment=dict(question="만기 2개월 됐을때의 이자율은?", status="supported",
                                 requires_external_verification=False, explanation="",
                                 requirements=[dict(question_quote="만기 2개월 됐을때의 이자율은?", requirement="r",
                                                    status="supported", supports=[
                                                        dict(source_id="E2", field="text_raw", quote="만기이자율 X 30%")])]))

    def transport(payload, key, timeout):
        seen["schema"] = payload["text"]["format"]["schema"]
        return dict(status="completed", usage={"input_tokens": 1, "output_tokens": 1}, model="m",
                    output=[{"type": "message", "content": [{"type": "output_text", "text": json.dumps(draft, ensure_ascii=False)}]}])

    result = generate("만기 2개월 됐을때의 이자율은?", [ROW], tmp_path / "a", allow_external=True, transport=transport,
                      input_usd_per_million=1, output_usd_per_million=1, presentation=True)
    assert {"answer_text", "alternatives"} <= set(seen["schema"]["properties"])
    assert result["answer_text_source"] == "model" and result["selection"]["evidence_ids"] == ["E2"]
    assert "enum" not in seen["schema"]["$defs"]["Requirement"]["properties"]["question_quote"]
    question = draft["assessment"]["question"]
    generate(question, [ROW], tmp_path / "comparison", allow_external=True, transport=transport,
             input_usd_per_million=1, output_usd_per_million=1, presentation=True,
             comparison_quotes=["만기 2개월", "만기 ... 이자율"])
    assert seen["schema"]["$defs"]["Requirement"]["properties"]["question_quote"]["enum"] == ["만기 2개월", question]
    draft.update(values_raw=["근거에 없는 99%"], alternatives=[dict(interpretation="다른 99%", evidence_ids=["E2"])])
    rejected = generate(question, [ROW], tmp_path / "rejected", allow_external=True, transport=transport,
                        input_usd_per_million=1, output_usd_per_million=1, presentation=True)
    assert rejected["selection"] == dict(evidence_ids=[], values_raw=[], abstained=True)
    assert "UNSUPPORTED_ANSWER_VALUE_OR_CITATION" in rejected["assessment_validation"]["reasons"]
    assert not rejected["answer_text"] and not rejected["alternatives"]
    assert not (tmp_path / "rejected" / "failure.json").exists()


def test_model_abstention_wins_over_listed_selections(tmp_path, monkeypatch):
    # 기준선 kb-20: abstained=True와 근거 선택을 함께 내 INCONSISTENT_ABSTENTION으로 실패했다.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-never-sent")
    draft = dict(evidence_ids=["E2"], values_raw=["만기이자율 X 30%"], abstained=True, answer_text="", alternatives=[],
                 assessment=dict(question="q", status="unsupported", requires_external_verification=False,
                                 explanation="", requirements=[dict(question_quote="q", requirement="r",
                                                                    status="unsupported", supports=[])]))

    def transport(payload, key, timeout):
        return dict(status="completed", usage={}, model="m", output=[{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps(draft, ensure_ascii=False)}]}])

    result = generate("q", [ROW], tmp_path / "a", allow_external=True, transport=transport,
                      input_usd_per_million=1, output_usd_per_million=1, presentation=True)
    assert result["selection"]["abstained"] and result["selection"]["evidence_ids"] == []
    assert (tmp_path / "a/ignored-selection-on-abstention.json").is_file()


def test_a_long_passage_chosen_as_evidence_need_not_be_copied_whole_into_the_sentence():
    from types import SimpleNamespace
    from doc_understanding.rag.generation.answer import present, said
    clause = "① 예금잔액이 10,000원 미만이며, 1년 이상 입출금거래가 없는 계좌"
    evidence = [dict(evidence_id="E1", value_raw=clause, row_path=["거래중지"], column_path=["내 용"])]
    selection = dict(evidence_ids=["E1"], values_raw=[clause], abstained=False)
    sentence = "예금잔액이 8,000원이고 2년 동안 입출금 거래가 없으면 거래가 제한됩니다."
    question = "예금잔액이 8000원이고 2년동안 입출금 거래가 없어도 거래를 계속 할 수 있나?"
    shown = present(SimpleNamespace(answer_text=sentence, alternatives=[]), selection, evidence, question)
    # 조문을 통째로 옮기지 않았어도, 조문의 구절을 담은 모델의 문장을 그대로 쓴다.
    assert shown["answer_text_source"] == "model" and shown["answer_text"] == sentence
    # 조문과 아무 글자도 겹치지 않는 문장은 그 조문을 근거로 한 문장이 아니다.
    other = present(SimpleNamespace(answer_text="가능합니다.", alternatives=[]), selection, evidence, question)
    assert other["answer_text_source"] == "template" and other["answer_text_reasons"] == ["SELECTED_VALUE_NOT_IN_TEXT"]
    # 근거에 없는 숫자가 든 문장은 여전히 버린다.
    invented = present(SimpleNamespace(answer_text="예금잔액이 50,000원 미만이면 거래가 제한됩니다.", alternatives=[]), selection, evidence, question)
    assert invented["answer_text_source"] == "template" and "UNSUPPORTED_NUMBER_IN_TEXT" in invented["answer_text_reasons"]
    # 짧은 값은 문장에 그대로 있어야 한다.
    assert said("2.85", "기본금리는 2.85입니다.") and not said("2.85", "기본금리는 2.8입니다.")
    assert not said("신용사업에 포함", "신용사업으로 봅니다.") and said("신용사업에 포함", "보험사업은 신용사업에 포함됩니다.")
