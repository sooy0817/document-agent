"""RAG 평가: 평가셋 검사·정답 위치 해석·지표. 검색·모델은 실행하지 않는다."""
from pathlib import Path

import pytest

from doc_understanding.evaluation.rag.anchors import resolve
from doc_understanding.evaluation.rag.dataset import EvalSet, load
from doc_understanding.evaluation.rag.metrics import score_item, summarize

ROOT = Path(__file__).resolve().parents[1]
DEV_SET = ROOT / "config/evaluation/rag-dev-v1.json"
KB_DOCUMENT = ROOT / "work/document-explorer/331a24bbcd404065b06605ee777fc3b2/document.json"


def tiny(**item):
    return EvalSet.model_validate(dict(name="t", status="draft", reviewed_by_human=False, documents={"d": "doc-1"},
                                       items=[dict(dict(id="q1", type="lookup", question="q",
                                                        gold=[dict(doc="d", page=1, text="x")],
                                                        expected=["x"]), **item)]))


def test_dev_set_is_a_valid_unreviewed_draft():
    data = load(DEV_SET)
    assert data.status == "draft" and data.reviewed_by_human is False and len(data.items) >= 30
    assert {"interval", "nested_scope", "multi_document", "ambiguous", "unanswerable"} <= {i.type for i in data.items}


@pytest.mark.parametrize("item,reason", [
    (dict(gold=[]), "ANSWERABLE_WITHOUT_GOLD:q1"),
    (dict(expected=[]), "ANSWERABLE_WITHOUT_EXPECTED:q1"),
    (dict(gold=[dict(doc="zz", page=1, text="x")]), "UNKNOWN_DOCUMENT:q1"),
    (dict(answerable=False), "UNANSWERABLE_WITH_GOLD:q1"),
])
def test_invalid_items_are_rejected(item, reason):
    assert reason in tiny(**item).check()


def test_dev_set_gold_resolves_on_local_kb_document():
    if not KB_DOCUMENT.is_file():
        pytest.skip("local KB baseline document is not available")
    from doc_understanding.domain.pipeline_contracts import CanonicalDocument
    doc = CanonicalDocument.model_validate_json(KB_DOCUMENT.read_bytes())
    data = load(DEV_SET)
    for item in data.items:
        for anchor in [*item.gold, *item.alternative_gold]:
            if anchor.doc == "kb":
                assert resolve(doc, anchor), item.id


def test_scores_rank_evidence_answer_and_abstention():
    data = tiny()
    item = data.items[0]
    resolved = dict(gold={"cell-x"}, alternative=set(), unresolved=[])
    run = dict(hits=[dict(payload=dict(cell_ids=["other"])), dict(payload=dict(cell_ids=["cell-x"]))],
               evidence=[dict(cell_id="cell-x")], seconds=1.0, cost_usd=0.01,
               result=dict(selection=dict(evidence_ids=["E1"], values_raw=["x"], abstained=False), answer_text="x"))
    row = score_item(item, resolved, run)
    assert row["first_gold_rank"] == 2 and row["hit@1"] is False and row["hit@3"] is True and row["mrr"] == 0.5
    assert row["evidence_recall"] and row["answer_correct"] and row["abstain_correct"]
    held = score_item(item, resolved, dict(run, result=dict(selection=dict(abstained=True, values_raw=[]))))
    assert held["answer_correct"] is False and held["abstain_correct"] is False
    summary = summarize([row, held])
    assert summary["hit@3"] == dict(n=2, rate=1.0) and summary["answer_correct"] == dict(n=2, rate=0.5)


def test_unanswerable_and_ambiguous_items_score_abstention_and_alternatives():
    un = tiny(gold=[], expected=[], answerable=False).items[0]
    row = score_item(un, dict(gold=set(), alternative=set(), unresolved=[]),
                     dict(hits=[], evidence=[], result=dict(selection=dict(abstained=True))))
    assert row["abstain_correct"] is True and "hit@1" not in row
    amb = tiny(ambiguous=True).items[0]
    row = score_item(amb, dict(gold={"x"}, alternative=set(), unresolved=[]),
                     dict(hits=[], evidence=[], result=dict(selection=dict(abstained=False, values_raw=["x"]),
                                                            alternatives=[dict(interpretation="i", evidence_ids=["E2"])])))
    assert row["ambiguity_flagged"] is False and "answer_correct" not in row


def test_maturity_rubric_requires_both_questions_or_a_source_for_the_stated_assumption():
    pilot = load(ROOT / "config/evaluation/rag-maturity-paired-dev-v1.json")
    amb, control = pilot.items[-2:]
    assert amb == next(item for item in load(DEV_SET).items if item.id == amb.id)
    resolved = dict(gold={"kb"}, gold_groups=[{"kb"}], alternative={"early", "sh"},
                    alternative_groups=[{"early"}, {"sh"}], unresolved=[])
    evidence = [dict(evidence_id="E1", cell_id="kb"), dict(evidence_id="E2", cell_id="sh")]

    def grade(text, ids=(), held=False):
        result = dict(answer_text=text, selection=dict(evidence_ids=list(ids), abstained=held))
        return score_item(amb, resolved, dict(evidence=evidence, result=result))["ambiguity_flagged"]

    assert grade("어느 은행 상품인가요? 만기 후 2개월이 지났다는 뜻인가요?", held=True)
    assert not grade("확인해 주세요.", held=True)
    assert not grade("어느 은행 상품인가요?", held=True)
    assert not grade("만기 후 2개월인가요?", held=True)
    assert not grade("이 상품의 만기 후 2개월인가요?", held=True)
    assert grade("어느 은행인지와 만기일 이후 2개월인지 알려 주세요.", held=True)
    conditional = "KB 상품의 만기 후 2개월이라면 만기이자율 × 30%입니다."
    assert grade(conditional, ["E1"]) and not grade(conditional, ["E2"])
    assert not grade("만기이자율 × 30%입니다.", ["E1"])
    assert not grade(conditional + " 기준 이율은 1.15%입니다.", ["E1"])
    sh = "수협 상품의 만기 후 2개월인 경우 만기후이율은 없음입니다."
    assert grade(sh, ["E2"]) and not grade(sh + " 따라서 0%입니다.", ["E2"])
    refs = dict(gold={"sh"}, gold_groups=[{"sh"}], unresolved=[])
    for selected, correct in [(["E2"], True), (["E1"], False), ([], False)]:
        result = dict(answer_text="없음", selection=dict(evidence_ids=selected, values_raw=["없음"]))
        assert score_item(control, refs, dict(evidence=evidence, result=result))["answer_correct"] is correct


@pytest.mark.parametrize(("selected", "expected_count"), [
    (["A", "B"], 2), (["A"], 1), (["B"], 1), (["A", "A"], 1),
    (["A", "wrong-row"], 1), (["A", "unknown"], 1), ([], 0),
])
def test_comparison_requires_selected_gold_from_both_documents(selected, expected_count):
    from doc_understanding.evaluation.rag.failures import item_failure
    item = tiny(type="multi_document", expected=["0.1"], gold=[
        dict(doc="left", page=2, text="0.1"), dict(doc="right", page=1, text="0.10")]).items[0]
    groups = [{"left-row"}, {"right-row", "right-passage"}]
    resolved = dict(gold=set.union(*groups), gold_groups=groups, unresolved=[])
    run = dict(evidence=[dict(evidence_id="A", cell_id="left-row"), dict(evidence_id="B", content_id="right-passage"),
                         dict(evidence_id="wrong-row", cell_id="other-right-row")],
               result=dict(answer_text="두 은행 모두 0.10%", selection=dict(evidence_ids=selected)))
    row = score_item(item, resolved, run, mode="answer")
    assert row["answer_string_match"] and row["all_required_evidence"]  # 검색 결과에는 양쪽 모두 있다.
    assert row["selected_gold_count"] == expected_count and row["required_gold_count"] == 2
    assert row["answer_correct"] is (expected_count == 2)
    assert summarize([row])["answer_totals"]["answerable"]["correct"] == int(expected_count == 2)
    failure = item_failure(item, row, run, "agent_answer", "review")
    if expected_count == 2:
        assert failure is None
        run["result"]["answer_text"] = "다른 값"
        assert not score_item(item, resolved, run)["answer_correct"]
    else:
        assert failure["stage"] == "selection" and failure["missing_selected_gold_docs"]


def test_comparison_cannot_credit_unresolved_or_ambiguous_evidence_ids():
    item = tiny(type="multi_document", gold=[dict(doc=d, page=1, text="x") for d in ("a", "b")]).items[0]
    run = dict(evidence=[dict(evidence_id="E1", cell_id="a"), dict(evidence_id="E1", cell_id="b")],
               result=dict(answer_text="x", selection=dict(evidence_ids=["E1"])))
    refs = dict(gold={"a", "b"}, gold_groups=[{"a"}, {"b"}], unresolved=[])
    assert not score_item(item, refs, run)["answer_correct"]
    run["evidence"][1]["evidence_id"] = "E2"
    run["result"]["selection"]["evidence_ids"].append("E2")
    assert not score_item(item, dict(refs, gold_groups=[{"a"}, set()]), run)["answer_correct"]
    del refs["gold_groups"]  # 예전 합집합만으로는 문서별 선택을 확인할 수 없다.
    assert not score_item(item, refs, run)["answer_correct"]


def test_row_label_separates_branches_with_identical_values():
    from tests.test_question_agent import reviewed_kb
    from doc_understanding.evaluation.rag.anchors import resolve
    from doc_understanding.evaluation.rag.dataset import Anchor
    doc = reviewed_kb()
    text = "회전기간별로 회전기간 시작일에 고시된 퇴직연금정기예금 기본 이자율"
    plain = resolve(doc, Anchor(doc="kb", page=3, text=text))
    after = resolve(doc, Anchor(doc="kb", page=3, text=text, row_label="만기 재예치 後"))
    before = resolve(doc, Anchor(doc="kb", page=3, text=text, row_label="최초 만기 前"))
    assert len(after) == 1 and len(before) == 1 and after != before and after | before <= plain


def test_forbidden_strings_catch_an_expected_value_found_by_accident():
    # '0.1'을 기대했는데 다른 행의 '… (단, 최저금리는 0.1)'을 고르면 기대 문자열이 우연히 들어 있어도 오답이다.
    from doc_understanding.evaluation.rag.dataset import EvalItem
    item = EvalItem(id="ct", type="nested_scope", question="q", expected=["0.1"], forbidden=["50%"])
    resolved = dict(gold=set(), alternative=set(), unresolved=[])
    wrong = dict(result=dict(selection=dict(values_raw=["만기이율 X 50% (단, 최저금리는 0.1)"]), answer_text=""))
    right = dict(result=dict(selection=dict(values_raw=["0.1"]), answer_text="1개월 미만은 0.1%입니다."))
    assert score_item(item, resolved, wrong)["answer_correct"] is False
    assert score_item(item, resolved, right)["answer_correct"] is True


def test_question_failures_name_the_stage_that_lost_the_answer():
    from doc_understanding.evaluation.rag.dataset import Anchor, EvalItem
    from doc_understanding.evaluation.rag.failures import item_failure
    item = EvalItem(id="q", type="lookup", question="q?", gold=[Anchor(doc="d", page=1, text="x")], expected=["x"])
    def stage(row, run):
        f = item_failure(item, row, run, "agent_answer", "set")
        return f and f["stage"]
    wrong = dict(result=dict(selection=dict(values_raw=["y"])), evidence=[dict(page=1, value_raw="y")])
    assert stage(dict(first_gold_rank=None, evidence_first_rank=None, answer_correct=False), wrong) == "retrieval"
    assert stage(dict(first_gold_rank=2, evidence_first_rank=None, answer_correct=False), wrong) == "evidence"
    assert stage(dict(first_gold_rank=2, evidence_first_rank=3, answer_correct=False), wrong) == "selection"
    assert stage(dict(first_gold_rank=1, evidence_first_rank=1, answer_correct=False), wrong) == "answer"
    assert stage(dict(first_gold_rank=1, evidence_first_rank=1, answer_correct=True), wrong) is None
    assert stage(dict(first_gold_rank=None), dict(error="ValueError", evidence=[])) == "answer_error"
    never = EvalItem(id="u", type="unanswerable", question="q?", answerable=False)
    assert item_failure(never, dict(abstain_correct=False), wrong, "agent_answer", "set")["stage"] == "abstention"
    assert item_failure(never, dict(abstain_correct=True), wrong, "agent_answer", "set") is None
    vague = EvalItem(id="a", type="ambiguous", question="q?", ambiguous=True)
    assert item_failure(vague, dict(ambiguity_flagged=False), wrong, "agent_answer", "set")["stage"] == "ambiguity"
    assert item_failure(vague, dict(ambiguity_flagged=True), wrong, "agent_answer", "set") is None
    # 검색만 한 설정: 답이 없으므로 검색·근거 단계만 본다.
    assert item_failure(item, dict(first_gold_rank=None, evidence_first_rank=None), dict(evidence=[]), "hybrid", "s")["stage"] == "retrieval"


def test_answer_totals_keep_run_errors_in_the_denominator():
    """실행 오류는 정답률 분모에서 빠지지 않는다. 대상·정상 완료·정답·보류·오류 수를 함께 낸다."""
    from doc_understanding.evaluation.rag.failures import item_failure
    item = tiny().items[0]
    resolved = dict(gold={"cell-x"}, alternative=set(), unresolved=[])
    base = dict(hits=[dict(payload=dict(cell_ids=["cell-x"]))], evidence=[dict(cell_id="cell-x")])
    right = dict(base, result=dict(selection=dict(values_raw=["x"], abstained=False), answer_text="x"))
    held = dict(base, result=dict(selection=dict(values_raw=[], abstained=True)))
    broken = dict(error="RuntimeError", error_message="boom", hits=[], evidence=[])
    rows = [score_item(item, resolved, run, mode="answer") for run in (right, held, broken, broken)]
    assert [r["run_error"] for r in rows] == [False, False, True, True]
    summary = summarize(rows)
    # 예전 지표: 정상 완료된 문항만 분모(1/2). 오류가 많을수록 부풀 수 있어 그대로 두되 전체 기준을 따로 낸다.
    assert summary["answer_correct"] == dict(n=2, rate=0.5)
    totals = summary["answer_totals"]
    assert summary["mode"] == "answer" and summary["run_errors"] == 2
    assert (totals["total"], totals["completed"], totals["run_errors"], totals["abstained"]) == (4, 2, 2, 1)
    assert totals["answerable"] == dict(total=4, completed=2, correct=1, abstained=1, wrong=0, run_errors=2,
                                        rate_of_total=0.25, rate_of_completed=0.5)
    assert totals["expected_outcome"] == dict(met=1, total=4, rate_of_total=0.25)
    assert item_failure(item, rows[2], broken, "agent_answer", "s")["stage"] == "answer_error"


def test_answer_mode_without_a_result_is_a_run_error_even_without_an_error_field():
    item = tiny().items[0]
    resolved = dict(gold={"cell-x"}, alternative=set(), unresolved=[])
    row = score_item(item, resolved, dict(hits=[], evidence=[]), mode="answer")
    assert row["run_error"] is True
    assert summarize([row])["answer_totals"]["answerable"]["rate_of_total"] == 0.0


def test_retrieval_only_runs_are_not_counted_as_answer_failures():
    """검색만 실행한 평가는 답변 집계를 만들지 않고, 실행 오류도 답변 오류로 부르지 않는다."""
    from doc_understanding.evaluation.rag.failures import item_failure
    from doc_understanding.evaluation.rag.metrics import mode_of
    item = tiny().items[0]
    resolved = dict(gold={"cell-x"}, alternative=set(), unresolved=[])
    found = dict(hits=[dict(payload=dict(cell_ids=["cell-x"]))], evidence=[dict(cell_id="cell-x")])
    row = score_item(item, resolved, found, mode=mode_of("agent_rerank"))
    assert row["mode"] == "retrieval" and row["run_error"] is False and "answer_correct" not in row
    summary = summarize([row])
    assert summary["mode"] == "retrieval" and summary["answer_totals"] is None and summary["run_errors"] == 0
    assert item_failure(item, row, found, "agent_rerank", "s") is None
    broken = dict(error="RuntimeError", hits=[], evidence=[])
    failed = score_item(item, resolved, broken, mode="retrieval")
    assert failed["run_error"] is True
    assert item_failure(item, failed, broken, "hybrid", "s")["stage"] == "run_error"
    assert mode_of("agent_answer") == "answer" and mode_of("custom", [dict(result={})]) == "answer"
    assert mode_of("custom", [dict(hits=[])]) == "retrieval"


def test_unanswerable_and_ambiguous_items_count_toward_the_expected_outcome():
    never = tiny(answerable=False, gold=[], expected=[]).items[0]
    vague = tiny(ambiguous=True).items[0]
    none = dict(gold=set(), alternative=set(), unresolved=[])
    held = dict(hits=[], evidence=[], result=dict(selection=dict(abstained=True, values_raw=[])))
    rows = [score_item(never, none, held, mode="answer"), score_item(vague, none, held, mode="answer"),
            score_item(never, none, dict(error="X"), mode="answer")]
    totals = summarize(rows)["answer_totals"]
    assert totals["answerable"]["total"] == 0 and totals["answerable"]["rate_of_total"] is None
    assert totals["expected_outcome"] == dict(met=1, total=3, rate_of_total=0.333) and totals["abstained"] == 2
