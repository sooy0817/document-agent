"""범용 질문 계약·출처 연결 회귀. 모든 의미 판단은 명시적 모의 응답이다."""

import copy
import json

import pytest

from doc_understanding.rag.evidence.assessment import validate_assessment
from doc_understanding.rag.evidence.validation import validate_evidence
from doc_understanding.rag.generation.answer import generate
from doc_understanding.rag.generation.narrative import build_catalog
from tests.rag_assessment_helpers import receipt


def evidence(value="Use dry storage.", **extra):
    return dict(
        evidence_id="E1",
        source_name="manual.pdf",
        page=1,
        table_id=None,
        value_raw=value,
        row_path=[],
        column_path=[],
        **extra,
    )


@pytest.mark.parametrize(
    "question", ["작년에는?", "How hot?", "Combien de jours ?", "어떻게 보관해?", "1800년 기록?"]
)
def test_preflight_never_claims_question_coverage(question):
    result = validate_evidence([evidence()], question)
    assert result["sufficient"] and result["question_coverage"] == "not_assessed"
    assert "periods" not in result


def test_preflight_rejects_missing_identity_and_unresolved_table():
    assert not validate_evidence([{}], "상태")["sufficient"]
    e = evidence()
    e["table_id"] = "T"
    assert not validate_evidence([e], "상태")["sufficient"]
    e["lookup_eligible"] = True
    assert validate_evidence([e], "상태")["sufficient"]
    assert not validate_evidence([e, e], "상태")["sufficient"]


@pytest.mark.parametrize(
    "value,question,table",
    [
        ("The trial lasted 17 days.", "How many days did the trial last?", False),
        ("윤활 후 재조립", "이 작업의 조치 방법은?", True),
        ("Durée : 17 jours.", "Quelle est la durée ?", False),
        ("1800년 관측 기록", "1800년 기록 내용을 알려줘", False),
    ],
)
def test_selection_accepts_source_types_without_question_dictionary(
    tmp_path, value, question, table
):
    e = evidence(value)
    if table:
        e.update(
            table_id="T",
            cell_id="C",
            row_path=["작업 A"],
            column_path=["조치"],
            lookup_eligible=True,
        )
    calls = []

    def transport(payload, *_):
        data = json.loads(payload["input"][1]["content"])
        calls.append(data)
        draft = dict(
            evidence_ids=["E1"],
            values_raw=[value],
            abstained=False,
            assessment=receipt(question, data["sources"]),
        )
        return dict(
            status="completed",
            usage={},
            output=[
                dict(type="message", content=[dict(type="output_text", text=json.dumps(draft))])
            ],
        )

    result = generate(question, [e], tmp_path / "answer", allow_external=True, transport=transport)
    assert len(calls) == 1 and not result["selection"]["abstained"]
    assert result["assessment_validation"]["passed"]
    assert result["assessment_validation"]["semantic_entailment_verified"] is False


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("question", "ASSESSMENT_QUESTION_MISMATCH"),
        ("question_span", "REQUIREMENT_NOT_ANCHORED_IN_QUESTION"),
        ("source", "UNKNOWN_ASSESSMENT_SOURCE"),
        ("quote", "ASSESSMENT_QUOTE_NOT_IN_SOURCE"),
        ("unsupported", "QUESTION_REQUIREMENTS_NOT_SUPPORTED"),
        ("ambiguous", "QUESTION_REQUIREMENTS_NOT_SUPPORTED"),
        ("external", "EXTERNAL_VERIFICATION_REQUIRED"),
        ("unselected", "ASSESSMENT_OUTSIDE_SELECTED_EVIDENCE"),
        ("extra_selection", "SELECTED_EVIDENCE_NOT_ASSESSED"),
    ],
)
def test_assessment_blocks_incomplete_or_unanchored_answer(mutation, reason):
    question = "How should it be stored?"
    catalog = build_catalog([evidence()])
    draft = receipt(question, catalog)
    selected = ["E1"]
    if mutation == "question":
        draft["question"] = "Another question"
    if mutation == "question_span":
        draft["requirements"][0]["question_quote"] = "invented subject"
    if mutation == "source":
        draft["requirements"][0]["supports"][0]["source_id"] = "invented"
    if mutation == "quote":
        draft["requirements"][0]["supports"][0]["quote"] = "Store wet."
    if mutation in ("unsupported", "ambiguous"):
        draft["requirements"][0]["status"] = mutation
    if mutation == "external":
        draft["requires_external_verification"] = True
    if mutation == "unselected":
        selected = []
    if mutation == "extra_selection":
        selected.append("E2")
    result = validate_assessment(draft, question, catalog, selected)
    assert not result["passed"] and reason in result["reasons"]


def test_does_not_treat_literal_anchor_as_semantic_truth():
    # 동일 연도라는 이유만으로 다른 항목이 맞는지는 이 검사기가 증명할 수 없다.
    catalog = build_catalog([evidence("Temperature in 2025: 20 C")])
    result = validate_assessment(
        receipt("2025 revenue?", catalog), "2025 revenue?", catalog, ["E1"]
    )
    assert result["passed"] and result["semantic_entailment_verified"] is False


def test_assessment_trace_keeps_reasons_and_masks_private_strings():
    from doc_understanding.runtime.telemetry import redact

    catalog = build_catalog([evidence("Email test@example.com")])
    assessment = validate_assessment(receipt("Contact?", catalog), "Contact?", catalog, ["E1"])
    safe = redact(dict(assessment_validation=assessment, api_key="sk-never-export-this"))
    assert safe["assessment_validation"]["passed"]
    assert safe["assessment_validation"]["assessment"]["requirements"][0]["supports"]
    assert "test@example.com" not in json.dumps(safe)
    assert "sk-never-export-this" not in json.dumps(safe)


def test_unavailable_raw_text_is_not_a_blank_cell():
    assert not validate_evidence([evidence(None)], "Explain")["sufficient"]
    assert validate_evidence([evidence("")], "Explain")["sufficient"]


@pytest.mark.parametrize(
    "external,status", [(True, "supported"), (False, "ambiguous"), (False, "unsupported")]
)
def test_generation_overrides_selection_when_assessment_does_not_support_it(
    tmp_path, external, status
):
    def transport(payload, *_):
        data = json.loads(payload["input"][1]["content"])
        draft = dict(
            evidence_ids=["E1"],
            values_raw=["17 days"],
            abstained=False,
            assessment=receipt(data["question"], data["sources"], status=status, external=external),
        )
        return dict(
            status="completed",
            output=[
                dict(type="message", content=[dict(type="output_text", text=json.dumps(draft))])
            ],
        )

    result = generate(
        "Duration?",
        [evidence("17 days")],
        tmp_path / "blocked",
        allow_external=True,
        transport=transport,
    )
    assert result["selection"] == dict(evidence_ids=[], values_raw=[], abstained=True)
    assert "17 days" not in result["answer"]
    assert not result["assessment_validation"]["passed"]


def test_context_preserves_each_carrier_instead_of_first_cell_only():
    first = evidence("A", nested_context={"prose": [{"text_raw": "Keep dry."}]})
    second = copy.deepcopy(first)
    second.update(evidence_id="E2", value_raw="B")
    catalog = build_catalog([first, second])
    parents = [s for s in catalog if s["kind"] == "parent_text"]
    assert [s["carrier_evidence_id"] for s in parents] == ["E1", "E2"]
    result = validate_assessment(receipt("Storage?", [parents[1]]), "Storage?", catalog, ["E2"])
    assert result["passed"]


@pytest.mark.parametrize("mode", ["selection", "narrative"])
def test_generation_without_assessment_cannot_emit_an_answer(tmp_path, mode):
    def transport(*_):
        draft = (
            dict(evidence_ids=["E1"], values_raw=["A"], abstained=False)
            if mode == "selection"
            else dict(
                target_term="", sentences=[], condition_assessments=[], abstained=False, reason=""
            )
        )
        return dict(
            status="completed",
            output=[
                dict(type="message", content=[dict(type="output_text", text=json.dumps(draft))])
            ],
        )

    if mode == "selection":
        with pytest.raises(RuntimeError, match="ANSWER_CALL_FAILED"):
            generate(
                "Explain",
                [evidence("A")],
                tmp_path / mode,
                allow_external=True,
                transport=transport,
            )
    else:
        result = generate(
            "Explain",
            [evidence("A")],
            tmp_path / mode,
            allow_external=True,
            answer_mode=mode,
            transport=transport,
        )
        assert result["selection"]["abstained"]
