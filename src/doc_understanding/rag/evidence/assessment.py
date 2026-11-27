# 역할: 질문별 모델의 근거 충족 판단을 구조화하고 원문 인용과 선택 근거의 연결을 검사한다.
"""Model assessment and source-anchor checks; not proof of semantic entailment."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Support(Strict):
    source_id: str = Field(min_length=1, max_length=200)
    field: Literal['text_raw', 'row_path', 'column_path']
    quote: str = Field(min_length=1, max_length=12000)


class Requirement(Strict):
    question_quote: str = Field(min_length=1, max_length=4000)
    requirement: str = Field(min_length=1, max_length=1000)
    status: Literal['supported', 'unsupported', 'ambiguous']
    supports: list[Support] = Field(max_length=40)


class QuestionAssessment(Strict):
    question: str = Field(min_length=1, max_length=12000)
    status: Literal['supported', 'unsupported', 'ambiguous']
    requires_external_verification: bool
    requirements: list[Requirement] = Field(min_length=1, max_length=40)
    explanation: str = Field(max_length=2000)


def validate_assessment(raw, question, catalog, selected_ids, *, answer_request=None):
    # 모델 판단의 형식과 인용 연결을 검사한다. 판단 자체의 의미 정확도를 인증하지 않는다.
    reasons = []
    sources = {s['source_id']: s for s in catalog}
    selected = set(selected_ids)
    cited_sources, carriers = set(), set()
    try:
        assessment = QuestionAssessment.model_validate(raw)
    except (ValueError, TypeError):
        return dict(passed=False, reasons=['INVALID_QUESTION_ASSESSMENT'],
                    semantic_entailment_verified=False, assessment=None, source_ids=[])
    if assessment.question != question:
        reasons.append('ASSESSMENT_QUESTION_MISMATCH')
    if answer_request is not None:
        quote, requirement = answer_request['question_quote'], answer_request['requirement']
        if not quote.strip() or quote not in question:
            reasons.append('ANSWER_REQUEST_NOT_ANCHORED_IN_QUESTION')
        if not any(r.question_quote == quote and r.requirement == requirement for r in assessment.requirements):
            reasons.append('ANSWER_REQUEST_CHANGED_OR_OMITTED')
    if assessment.status != 'supported':
        reasons.append('QUESTION_REQUIREMENTS_NOT_SUPPORTED')
    if assessment.requires_external_verification:
        reasons.append('EXTERNAL_VERIFICATION_REQUIRED')
    if len(sources) != len(catalog):
        reasons.append('DUPLICATE_SOURCE_ID')
    for requirement in assessment.requirements:
        if not requirement.question_quote.strip() or requirement.question_quote not in question:
            reasons.append('REQUIREMENT_NOT_ANCHORED_IN_QUESTION')
        if requirement.status != 'supported' or not requirement.supports:
            reasons.append('QUESTION_REQUIREMENTS_NOT_SUPPORTED')
        for support in requirement.supports:
            source = sources.get(support.source_id)
            if source is None:
                reasons.append('UNKNOWN_ASSESSMENT_SOURCE')
                continue
            values = source.get(support.field, [])
            values = [values] if isinstance(values, str) else values
            if not support.quote.strip() or not any(support.quote in v for v in values if isinstance(v, str)):
                reasons.append('ASSESSMENT_QUOTE_NOT_IN_SOURCE')
            if not source.get('lookup_eligible'):
                reasons.append('UNRESOLVED_LOOKUP_BLOCKED')
            carrier = source['carrier_evidence_id']
            if carrier not in selected:
                reasons.append('ASSESSMENT_OUTSIDE_SELECTED_EVIDENCE')
            carriers.add(carrier)
            cited_sources.add(support.source_id)
    if selected - carriers:
        reasons.append('SELECTED_EVIDENCE_NOT_ASSESSED')
    return dict(passed=not reasons, reasons=sorted(set(reasons)),
                semantic_entailment_verified=False, assessment=assessment.model_dump(),
                source_ids=sorted(cited_sources),
                scope='schema, question spans, exact source anchors, selected evidence coverage')
