"""의미 기반 구조 일치: 표기 정규화·숫자 구간·의미 유사도. 외부 호출 없음(임베딩은 가짜)."""
import math

import pytest

from doc_understanding.rag.agentic.matching import (
    Interval,
    interval_relation,
    match_term,
    parse_interval,
    similarity_from,
    text_match,
)


@pytest.mark.parametrize("text,low,low_incl,high,high_incl", [
    ("만기후 1개월초과~3개월이내", 1, False, 3, True),
    ("1개월∼ 3개월 미만", 1, True, 3, False),
    ("01개월이상-06개월미만", 1, True, 6, False),
    ("12개월이상", 12, True, math.inf, True),
    ("계약기간 60개월 이하", -math.inf, True, 60, True),
    ("1년 미만", -math.inf, True, 12, False),
    ("6개월", 6, True, 6, True),
])
def test_interval_formats_found_in_documents(text, low, low_incl, high, high_incl):
    i = parse_interval(text)
    assert (i.low, i.low_incl, i.high, i.high_incl) == (low, low_incl, high, high_incl)


@pytest.mark.parametrize("text", ["이자율 (연%)", "최초 만기 前", "구분", "1.15"])
def test_text_without_a_quantity_has_no_interval(text):
    assert parse_interval(text) is None


@pytest.mark.parametrize("term,label,relation,approx", [
    ("만기 2개월", "만기후 1개월초과~3개월이내", "contains", False),
    ("만기 2개월", "만기후 1개월이내", "disjoint", False),
    ("만기 2개월", "만기후 3개월초과", "disjoint", False),
    ("7개월", "6개월이상~8개월미만", "contains", False),
    ("12개월", "1년 미만", "disjoint", False),   # 1년=12개월은 정확히 비교한다
    ("500일", "12개월∼18개월 미만", "contains", True),  # 일↔개월은 근사로 표시
    ("15일 미만", "15일 이상~1개월 미만", "disjoint", True),
    ("3%", "3개월", None, False),  # 차원이 다르면 비교하지 않는다
])
def test_interval_relations_respect_boundaries(term, label, relation, approx):
    assert interval_relation(term, label) == (relation, approx if relation else False)


def test_boundary_inclusion_is_exact():
    assert Interval(1, True, 3, False).contains(Interval(1, True, 1, True))
    assert not Interval(1, False, 3, True).contains(Interval(1, True, 1, True))
    assert not Interval(1, True, 3, False).overlaps(Interval(3, True, 3, True))


def test_spacing_and_symbol_variants_match_but_hanja_is_left_to_models():
    assert text_match("가입금액", "가 입 금 액") and not text_match("가입금액", "이자지급방식")
    assert parse_interval("1개월〜3개월 미만") == parse_interval("1개월~3개월 미만")
    # 한자 독음은 규칙으로 바꾸지 않는다. 라벨 임베딩·리랭커가 해석한다(matching.py 주석).
    assert not text_match("최초만기 전", "최초 만기 前")
    assert text_match("최초 만기 前", "최초 만기 前")


def test_exclusion_beats_text_and_interval_beats_text():
    labels = ["만기후 1개월이내", "이자율"]
    assert match_term("만기 2개월", labels)["kind"] == "interval_exclusion"
    best = match_term("만기 2개월", ["만기후 1개월초과~3개월이내", "이자율"])
    assert best["kind"] == "interval" and best["weight"] == 2.0


def test_semantic_similarity_is_weak_and_thresholded():
    vectors = {"중도해지 이율": [1, 0.0], "일반중도해지 이자율": [1, 0.6], "수수료": [0, 1]}
    sim = similarity_from(lambda texts: [vectors[t] for t in texts], "p", list(vectors))
    m = match_term("중도해지 이율", ["일반중도해지 이자율"], sim)
    assert m["kind"] == "semantic" and 0.5 <= m["weight"] <= 1.0
    assert match_term("중도해지 이율", ["수수료"], sim) is None


def test_similarity_failure_falls_back_to_text_and_interval_only():
    def broken(texts):
        raise RuntimeError("EMBEDDING_CALL_FAILED_SEE_AUDIT")
    assert similarity_from(broken, "p-fail", ["a", "b"]) is None


def test_kb_maturity_two_months_row_wins_by_interval():
    from tests.test_question_agent import _table, reviewed_kb
    from doc_understanding.rag.agentic.tools import structure_matches
    doc = reviewed_kb()
    rows = structure_matches(doc, _table(doc, "p0005/physical002"), ["만기 2개월", "이자율"])
    assert rows[0]["value_raw"].strip() == "만기이자율 X 30%" and rows[0]["match_score"] > rows[1]["match_score"]
    kinds = {m["kind"] for r in rows for m in r["match_details"]}
    assert {"interval", "interval_exclusion"} <= kinds
