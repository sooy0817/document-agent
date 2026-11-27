"""문서 고르기: 질문의 대상 문구를 문서 이름과 글자로 맞춘다. 이름은 모두 지어낸 것이다."""
from doc_understanding.rag.agentic.documents import (
    check_citations, coverage, resolve, select, within_one_edit, words,
)


def doc(run_id, organization="", title="", name="input.pdf", aliases=(), **extra):
    return dict(run_id=run_id, document_id="doc-" + run_id, name=name, source_name="input.pdf", pages=3,
                organization=organization, title=title, document_type="", as_of="",
                aliases=[dict(text=t, origin=o) for t, o in aliases], **extra)


DOCS = [
    doc("a1", "가람은행", "가람 정기예금 상품설명서", aliases=[("GARAM", "model_unverified"), ("가람뱅크", "model")]),
    doc("a2", "가람은행", "가람 자유적금 상품설명서"),
    doc("b1", "나래농업협동조합", "나래농협 요약 경영공시", aliases=[("나래농협", "model")]),
    doc("b2", "나루농업협동조합", "나루농협 요약 경영공시", aliases=[("나루농협", "model")]),
    doc("c1", "", "퇴직연금 정기예금 상품설명서"),
    doc("d1", "LH", "행복주택 입주자 모집"),
]
BODY = {"순자본비율": {"b1", "b2"}, "가람": {"a1", "a2"}, "정기예금": {"a1", "c1"}, "다솜은행": {"c1"}}


def mentions(word, run_id=None):
    found = BODY.get(word, set())
    return run_id in found if run_id else bool(found)


def status(subjects):
    result = select(subjects, DOCS, mentions)
    return result["status"], result["run_ids"]


def test_words_and_edits():
    assert words("「가람 정기예금」(요약) KB-1") == ["가람", "정기예금", "요약", "kb", "1"]
    assert within_one_edit("가람뱅크", "가람벵크") and within_one_edit("가람은행", "가람은") and not within_one_edit("가람", "가람")
    assert not within_one_edit("나래농협", "나루농업")


def test_one_document_is_selected_by_organisation_and_title_together():
    assert status(["가람은행 정기예금"]) == ("selected", ["a1"])
    assert status(["가람 자유적금"]) == ("selected", ["a2"])
    # 띄어쓰기·기호·대소문자 차이는 무시한다. 원문에 나오는 다른 표기도 강한 근거다.
    assert status(["나래 농협"]) == ("selected", ["b1"]) and status(["「가람뱅크」 정기예금"]) == ("selected", ["a1"])


def test_coordinated_qualifier_must_be_supported_in_each_named_document():
    docs = [doc("a1", "가람", "가람 정기 상품"), doc("a2", "가람", "자유 상품"), doc("b", "나래", "나래 안내")]
    question = "가람과 나래 정기 상품의 금액은 각각 얼마인가요?"
    subjects = ["나래 정기 상품", "가람 정기 상품"]
    def in_body(word, run_id=None):
        return run_id == "b" and word in ("정기", "상품")
    result = select(subjects, docs, in_body, question=question)
    assert result["status"] == "selected" and result["run_ids"] == ["b", "a1"]
    assert check_citations(result, ["a1"], docs, lambda *args: True) == (False, ["나래 정기 상품"])
    assert select(subjects, docs, lambda *args: False, question=question)["status"] != "selected"
    assert select(["없는기관 정기 상품", "나래 정기 상품"], docs, in_body,
                  question=question)["subjects"][0]["subject"] == "나래 정기 상품"


def test_names_one_letter_apart_stay_apart():
    assert status(["나래농협"]) == ("selected", ["b1"]) and status(["나루농협"]) == ("selected", ["b2"])
    # 짧은 이름은 한 글자 차이를 오타로 보지 않는다(다른 기관일 수 있다).
    assert resolve("나리농협", DOCS, mentions)["status"] != "selected"


def test_a_few_equal_matches_ask_back_and_many_narrow_the_search():
    result = select(["가람은행"], DOCS, mentions)
    assert result["status"] == "needs_choice" and [c["run_id"] for c in result["candidates"]] == ["a1", "a2"]
    assert result["candidates"][0]["organization"] == "가람은행" and result["subject"] == "가람은행"
    many = DOCS + [doc(f"e{i}", "가람은행", f"가람 상품 {i}") for i in range(5)]
    wide = select(["가람은행"], many, mentions)
    assert wide["status"] == "selected" and len(wide["run_ids"]) == 7


def test_short_words_match_only_at_the_edge_of_a_name_word():
    # 두 글자 낱말이 긴 낱말의 한가운데에 우연히 들어 있다고 맞은 것으로 보지 않는다.
    other = DOCS + [doc("z1", "", "ALHAMBRA 안내서")]
    assert status(["LH"]) == ("selected", ["d1"]) and select(["LH"], other, mentions)["run_ids"] == ["d1"]
    # 낱말의 앞·뒤에 붙은 경우는 맞는다(붙여 쓴 상품 이름의 끝말, 줄여 부른 기관 이름).
    savings = DOCS + [doc("s1", "가람은행", "가람 푸른하늘적금"), doc("s2", "가람은행", "가람 ONE 적금 상품설명서")]
    assert select(["가람은행 적금"], savings, mentions)["status"] == "needs_choice"
    assert [c["run_id"] for c in select(["가람은행 적금"], savings, mentions)["candidates"]] == ["a2", "s1", "s2"]
    assert select(["가람 정기예금"], DOCS, mentions)["run_ids"] == ["a1"]


def test_weak_evidence_does_not_narrow():
    # 모델이 추정한 표기(원문에 없음)로만 맞는다 → 좁히지 않는다.
    assert status(["GARAM 정기예금"]) == ("unconfirmed", None)
    # 긴 이름의 한 글자 오타 → 약한 맞춤.
    typo = DOCS + [doc("t1", "다온저축은행", "다온 정기예금")]
    assert select(["다온저측은행"], typo, mentions)["status"] == "unconfirmed"
    # 기관은 있는데 그 상품 문서는 없다 → 좁히지 않는다.
    assert status(["가람은행 외화예금"])[0] in ("unconfirmed", "not_found")


def test_subject_absent_from_names_and_bodies_is_reported_missing():
    result = select(["마루뱅크 정기예금"], DOCS, mentions)
    assert result["status"] == "not_found" and result["subject"] == "마루뱅크 정기예금"
    assert result["subjects"][0]["missing"] == ["마루뱅크"]
    assert {c["run_id"] for c in result["similar"]} <= {"a1", "c1"} and result["similar"]


def test_a_phrase_found_only_in_bodies_is_not_treated_as_a_missing_document():
    # 모델이 항목 이름을 대상으로 잘못 뽑아도 '문서 없음'이 되지 않는다. 좁히지 않고 인용 확인만 한다.
    assert status(["순자본비율"]) == ("unconfirmed", None)
    # 이름표에 적히지 않았지만 본문에 나오는 기관도 같다.
    assert status(["다솜은행"]) == ("unconfirmed", None)


def test_no_subject_and_several_subjects():
    assert status([]) == ("none", None) and status(["", "  "]) == ("none", None)
    assert status(["나래농협", "나루농협"]) == ("selected", ["b1", "b2"])
    assert status(["나래농협", "마루뱅크"])[0] == "not_found"
    assert status(["나래농협", "가람은행"])[0] == "needs_choice"
    assert status(["나래농협", "순자본비율"]) == ("unconfirmed", None)


def test_citation_check_needs_a_cited_document_that_mentions_the_subject():
    chosen = select(["다솜은행"], DOCS, mentions)
    assert check_citations(chosen, ["c1"], DOCS, mentions) == (True, [])
    assert check_citations(chosen, ["a1", "b1"], DOCS, mentions) == (False, ["다솜은행"])
    # 대상의 낱말을 모두 한 문서가 언급해야 한다(낱말마다 다른 문서로 채우지 않는다).
    both = dict(subjects=[dict(subject="가람 순자본비율", status="unconfirmed")])
    assert check_citations(both, ["a1", "b1"], DOCS, mentions) == (False, ["가람 순자본비율"])
    # 이름으로도 통과한다(본문 조회 없이).
    named = dict(subjects=[dict(subject="나래농협", status="unconfirmed")])
    assert check_citations(named, ["b1"], DOCS, None) == (True, []) and check_citations(named, ["b2"], DOCS, None)[0] is False


def test_coverage_counts_words_covered_by_strong_and_weak_names():
    strong, loose, parts = coverage("GARAM 정기예금", DOCS[0])
    assert parts == ["garam", "정기예금"] and strong == ["정기예금"] and loose == ["garam", "정기예금"]
