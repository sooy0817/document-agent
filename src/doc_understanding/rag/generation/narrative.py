# 역할: 문장형 답변의 인용·숫자·조건 보존을 검사한다. 의미 정답 승인은 하지 않는다.
"""Cited generated sentences; source fidelity checks, NOT a semantic truth judge."""

import re
from typing import Literal
from doc_understanding.rag.prompts.narrative import SYSTEM as SYSTEM
from pydantic import BaseModel, ConfigDict
from doc_understanding.rag.evidence.assessment import (
    QuestionAssessment,
    validate_assessment,
)
from doc_understanding.rag.evidence.validation import lookup_eligible


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Citation(Strict):
    source_id: str
    quote: str


class Sentence(Strict):
    text: str
    kind: Literal["value", "condition", "exclusion", "context"]
    citations: list[Citation]


class Assessment(Strict):
    source_id: str
    applicability: Literal["direct", "general_context", "excluded", "uncertain"]
    sentence: Sentence


class NarrativeAnswer(Strict):
    assessment: QuestionAssessment
    target_term: str
    sentences: list[Sentence]
    condition_assessments: list[Assessment]
    abstained: bool
    reason: str


def clause(raw):
    return re.sub(r"^\s*[-•■▪]\s*", "", raw).strip()


def requirements(question, catalog):
    # 문맥의 종류만 전달한다. 특정 단어로 조건 질문이나 적용 범위를 판정하지 않는다.
    return dict(
        context_source_ids=[
            s["source_id"]
            for s in catalog
            if s["kind"] in ("parent_text", "note", "unit_context", "table_context", "scope_context")
        ]
    )


def build_catalog(evidence):
    catalog = []
    seen = set()
    counters = {"P": 0, "U": 0, "N": 0, "H": 0, "S": 0}
    for e in evidence:
        common = {
            k: e.get(k)
            for k in (
                "document_id",
                "source_name",
                "document_label",
                "page",
                "table_id",
                "cell_id",
                "content_id",
                "span_refs",
                "source",
            )
        }
        catalog.append(
            dict(
                **common,
                source_id=e["evidence_id"],
                carrier_evidence_id=e["evidence_id"],
                kind="table_cell" if e.get("table_id") else "body",
                text_raw=e.get("value_raw") or "",
                row_path=e.get("row_path", []),
                column_path=e.get("column_path", []),
                lookup_eligible=lookup_eligible(e),
            )
        )
        context = e.get("nested_context") or {}
        for prefix, kind, items in [
            # 칸 안에 담긴 표: 담은 칸 라벨 경로·바깥 칸 문장·칸 안 문장(모두 원문, 적용 범위 미검증).
            # 라벨은 표시용 경로가 아니라 라벨 칸 원문 하나하나를 그 칸 출처와 함께 인용 후보로 둔다.
            # 같은 행·같은 경로의 나란한 칸(값 묶음)의 원문도 이 값의 문맥으로 둔다.
            ("P", "parent_text", list(e.get("group_values") or [])
             + [label for p in context.get("path", []) for label in p.get("labels", [])]
             + context.get("outer_prose", []) + context.get("prose", [])),
            ("U", "unit_context", e.get("unit_context", [])),
            ("N", "note", e.get("notes", [])),
            ("H", "table_context", [{"text_raw": t} for t in e.get("table_context", [])]),
            # 구조 검토 후보의 적용 범위 제목. 인용은 가능하지만 사람 승인 전 후보다.
            ("S", "scope_context", e.get("scope_context", [])),
        ]:
            for item in items:
                # 같은 문맥도 셀마다 연결을 보존한다. 첫 셀의 문맥으로 잘못 귀속하지 않는다.
                raw = item["text_raw"]
                identity = (
                    e["evidence_id"],
                    kind,
                    item.get("content_id"),
                    item.get("cell_id"),
                    raw,
                    context.get("parent_cell_id"),
                )
                if identity in seen:
                    continue
                seen.add(identity)
                counters[prefix] += 1
                catalog.append(
                    dict(
                        source_id=prefix + str(counters[prefix]),
                        kind=kind,
                        text_raw=raw,
                        carrier_evidence_id=e["evidence_id"],
                        document_id=e.get("document_id"),
                        source_name=e.get("source_name"),
                        document_label=e.get("document_label"),
                        page=item.get("page", context.get("page", e.get("page"))),
                        content_id=item.get("content_id"),
                        cell_id=item.get("cell_id"),
                        parent_cell_id=context.get("parent_cell_id"),
                        span_refs=item.get("span_refs", []),
                        source_references=item.get("source_references", []),
                        lookup_eligible=lookup_eligible(e),
                    )
                )
    return catalog


def numbers(text):
    return set(re.findall(r"(?<![\dA-Za-z])[-+△▲]?\d[\d,]*(?:\.\d+)?%?", text))


def validate_render(draft, catalog, question, evidence, *, answer_request=None):
    reasons = []
    records = []
    by_id = {s["source_id"]: s for s in catalog}
    try:
        d = NarrativeAnswer.model_validate(draft)
    except Exception:
        d = None
        reasons.append("INVALID_SENTENCE_SCHEMA")
    if d and d.abstained:
        if d.sentences or d.condition_assessments:
            reasons.append("INCONSISTENT_ABSTENTION")
    elif d:
        all_sentences = d.sentences + [a.sentence for a in d.condition_assessments]
        if not all_sentences:
            reasons.append("EMPTY_SENTENCES")
        if d.target_term and d.target_term not in question:
            reasons.append("TARGET_NOT_IN_QUESTION")
        # 적용 범위는 모델 후보로 남긴다. 코드는 ID·인용·미해결 여부만 검사한다.
        assessments = {a.source_id: a.applicability for a in d.condition_assessments}
        if len(assessments) != len(d.condition_assessments):
            reasons.append("DUPLICATE_ASSESSMENT")
        for item in d.condition_assessments:
            source = by_id.get(item.source_id)
            # scope_context는 표가 속한 소제목(구조 검토 후보)이며 부모 셀 문맥과 같은 조건 근거로 다룬다.
            if not source or source["kind"] not in ("parent_text", "note", "unit_context", "scope_context"):
                reasons.append("INVALID_CONDITION_SOURCE")
            if item.source_id not in {c.source_id for c in item.sentence.citations}:
                reasons.append("CONDITION_SENTENCE_MISMATCH")
            expected = "exclusion" if item.applicability == "excluded" else "condition"
            if item.sentence.kind != expected:
                reasons.append("CONDITION_SENTENCE_MISMATCH")
            if item.applicability == "uncertain":
                reasons.append("UNRESOLVED_APPLICABILITY")
        for i, sentence in enumerate(all_sentences, 1):
            errors = []
            allowed = ""
            sources = []
            if not sentence.citations:
                errors.append("MISSING_SENTENCE_CITATION")
            for citation in sentence.citations:
                source = by_id.get(citation.source_id)
                if not source:
                    errors.append("UNKNOWN_SOURCE")
                    continue
                sources.append(source)
                if not citation.quote.strip() or citation.quote not in source["text_raw"]:
                    errors.append("QUOTE_NOT_IN_SOURCE")
                if not source["lookup_eligible"]:
                    errors.append("UNRESOLVED_LOOKUP_BLOCKED")
                if source["kind"] == "table_cell":
                    if citation.quote != source["text_raw"]:
                        errors.append("VALUE_QUOTE_MISMATCH")
                    if re.sub(r"\s+", "", source["text_raw"]) not in re.sub(
                        r"\s+", "", sentence.text
                    ):
                        errors.append("VALUE_NOT_IN_SENTENCE")
                if sentence.kind in ("condition", "exclusion"):
                    full = clause(source["text_raw"])
                    if citation.quote.strip() != full or full not in sentence.text:
                        errors.append("CONDITION_QUOTE_NOT_PRESERVED")
                allowed += (
                    " "
                    + citation.quote
                    + " "
                    + " ".join(source.get("row_path", []) + source.get("column_path", []))
                )
            if numbers(sentence.text) - numbers(allowed):
                errors.append("UNSUPPORTED_NUMBER")
            records.append(
                dict(
                    sentence_id=f"S{i}",
                    sentence_text=sentence.text,
                    kind=sentence.kind,
                    citations=[c.model_dump() for c in sentence.citations],
                    sources=sources,
                    passed=not errors,
                    reasons=errors,
                )
            )
            reasons.extend(errors)
    used = {s["carrier_evidence_id"] for record in records for s in record["sources"]}
    assessment_validation = validate_assessment(
        d.assessment.model_dump() if d else None, question, catalog, used, answer_request=answer_request
    )
    reasons.extend(assessment_validation["reasons"])
    cited = {c["source_id"] for record in records for c in record["citations"]}
    if set(assessment_validation["source_ids"]) - cited:
        reasons.append("ASSESSED_SOURCE_NOT_CITED")
    reasons = sorted(set(reasons))
    blocked = bool(reasons) or not d or d.abstained
    selected = [e for e in evidence if e["evidence_id"] in used] if not blocked else []
    selection = dict(
        evidence_ids=[e["evidence_id"] for e in selected],
        values_raw=[e["value_raw"] for e in selected],
        abstained=blocked,
    )
    if blocked:
        answer = "조회한 근거와 문장별 검증만으로 답변을 확정할 수 없어 보류했습니다."
    else:
        labels = {
            "direct": "대상 조건 후보",
            "excluded": "대상 제외 후보",
            "general_context": "별도 문맥 조건",
        }
        scopes = {a.source_id: a.applicability for a in d.condition_assessments}
        lines = []
        for r in records:
            scope = next(
                (scopes[c["source_id"]] for c in r["citations"] if c["source_id"] in scopes), None
            )
            prefix = f"[{labels[scope]}] " if scope in labels else ""
            lines.append(
                prefix
                + r["sentence_text"]
                + " ["
                + ", ".join(c["source_id"] for c in r["citations"])
                + "]"
            )
        footnotes = []
        cited_ids = list(dict.fromkeys(c["source_id"] for r in records for c in r["citations"]))
        for sid in cited_ids:
            s = by_id[sid]
            ref = (
                s.get("cell_id")
                or s.get("content_id")
                or s.get("parent_cell_id")
                or s["carrier_evidence_id"]
            )
            short_ref = "/".join(ref.split("/")[-2:])
            footnotes.append(f"[{sid}] {s['source_name']}, p.{s['page']} · {short_ref}")
        answer = "\n\n".join(lines) + "\n\n출처:\n" + "\n".join(footnotes)
        answer += "\n\n이 답변은 해당 문서의 기재 내용에 대한 설명입니다. 현재 적용 여부와 추출된 의미 관계 전체는 검증하지 않았습니다."
    return dict(
        answer=answer,
        selection=selection,
        sentences=records,
        assessment_validation=assessment_validation,
        condition_assessments=[a.model_dump() for a in d.condition_assessments] if d else [],
        sentence_validation=dict(
            passed=not reasons and bool(d) and not d.abstained,
            reasons=reasons or (["MODEL_ABSTAINED"] if blocked else []),
            semantic_entailment_verified=False,
            scope="exact citations, values, numeric tokens, preserved clauses, assessed source coverage",
        ),
        generation_mode="llm_cited_sentences_with_source_checks",
    )
