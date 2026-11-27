# 역할: 제한된 모델 호출로 근거를 선택하고 원문 값·인용을 검사하여 답변을 출력한다.
"""One bounded model call: evidence selection + deterministic grounded rendering."""

import hashlib
import json
import os
import re
from pathlib import Path
from time import perf_counter

from doc_understanding.rag.prompts.selection import COMPARISON_INSTRUCTIONS, SYSTEM as SYSTEM
from pydantic import BaseModel, ConfigDict

from doc_understanding.agents.semantic_resolver.providers import http_transport
from doc_understanding.rag.generation.model_policy import rates, cost
from doc_understanding.rag.evidence.assessment import (
    QuestionAssessment,
    validate_assessment,
)
from doc_understanding.rag.evidence.periods import period_conflict
from doc_understanding.rag.evidence.validation import lookup_eligible
from doc_understanding.runtime.structured_call import NO_TEMPERATURE_PREFIXES


class AnswerSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    evidence_ids: list[str]
    values_raw: list[str | None]
    abstained: bool


class GroundedSelection(AnswerSelection):
    # 모델 응답에는 선택 결과뿐 아니라 질문 조건별 근거 판단도 반드시 포함한다.
    assessment: QuestionAssessment


class Alternative(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    interpretation: str
    evidence_ids: list[str]


class PresentedSelection(GroundedSelection):
    # 화면용: 짧은 답변 문장과, 질문이 모호할 때 다른 해석. 코드가 값·숫자·근거 ID를 검사한다.
    answer_text: str
    alternatives: list[Alternative]


PRESENTATION_INSTRUCTIONS = """
Also return answer_text: one or two short sentences in the question's language that answer it
directly. Include every selected value exactly as selected; do not add numbers, units or conditions
that are not in the selected evidence. If the question can reasonably be read another way that the
supplied evidence answers differently, list each reading in alternatives with the carrier
evidence_ids that support it; otherwise return an empty list. When a selected table cell holds
a long text and the question asks for part of it, values_raw may be that exact contiguous part
(copied character for character, never cutting a number or word).
If a source joins a term directly to preceding letters without whitespace (including unspaced
Korean), select the entire source text instead of cutting inside that word. Include that exact
short quotation in answer_text if needed. A named method must be linked to the action/conditions
in the question; cite the adjacent definition as well when the name line only says "this".
If abstaining, answer_text is empty and alternatives may explain the competing readings.
"""


def _norm(text):
    return re.sub(r"\s+", "", text or "")


# 잘리면 다른 값이 되는 덩어리: 숫자(1,000 · 0.10 · 1/10 · 2025.12 · 1e10)와 영문·숫자가 붙은 낱말(Q3, 10bp).
NUMBER_OR_WORD = re.compile(r"(?:\d+(?:[.,/:]\d+)*|(?<!\d)\.\d+)(?:[eE][-+]?\d+)?|[A-Za-z]+")
SIGNS, MARKS = "-−–+", "△▲▽▼"


def unsplittable(text):
    """원문에서 중간을 자르면 안 되는 구간들. 숫자는 부호·괄호 음수 표기까지 한 덩어리다.

    - 붙어 있는 영문·숫자는 한 낱말이다(1e10, Q3, 10bp). 띄어쓰기 없이 부호로 이어진 것도 한 낱말이다
      (non-refundable, 2024-2025, COVID-19): 뒷부분만 떼면 뜻이 달라질 수 있다.
    - 숫자 앞의 부호(-2.2, - 2.2, +3, income -2.2)와 증감 기호(△2,170, △ 55)는 숫자에 포함한다. 범위 표기인지
      음수인지 가릴 수 없는 경우(2021 - 2022)에도 부호를 뗀 숫자만 고를 수는 없다.
    - 숫자만 든 괄호((2,170))는 음수 표기일 수 있어 괄호째 한 덩어리다.
    """
    spans = []
    for m in NUMBER_OR_WORD.finditer(text):
        gap = text[spans[-1][1]:m.start()] if spans else None
        if spans and (gap == "" or (len(gap) == 1 and gap in SIGNS)):
            spans[-1][1] = m.end()
        else:
            spans.append([m.start(), m.end()])
    out = []
    for a, b in spans:
        if any(ch.isdigit() for ch in text[a:b]):
            j = a - 1 if a > 0 and text[a - 1] == " " else a
            if j > 0 and text[j - 1] in MARKS:
                a = j - 1
            elif j > 0 and text[j - 1] in SIGNS:
                a = j - 1
            left = a - 1 if a > 0 and text[a - 1] == " " else a
            right = b + 1 if b < len(text) and text[b] == " " else b
            if left > 0 and text[left - 1] == "(" and right < len(text) and text[right] == ")":
                a, b = left - 1, right + 1
        out.append((a, b))
    return out


def cell_line(raw, value):
    """선택 값이 원문 전체이거나 원문의 이어진 일부이면 인정한다(새 값을 만들 수 없다).

    일부 인용은 공백 차이만 무시하고 글자가 그대로 이어져야 하며, 뜻이 달라지게 자르면 안 된다.
    - 숫자·영문 낱말을 중간에서 자르지 못한다('0.10'에서 '0.1', '-2.2'에서 '2.2', '1,000'에서 '000').
    - 낱말의 앞부분을 떼어 내고 시작하지 못한다('불가능'에서 '가능', '미해당'에서 '해당'). 뒤에 붙은 조사는 뗄 수 있다.
    긴 본문·여러 줄 셀에서 답에 해당하는 부분만 고를 수 있게 하려는 것이다.
    """
    if not isinstance(value, str) or raw is None:
        return False
    if raw == value:
        return True
    part, text = " ".join(raw.split()), " ".join(value.split())
    if not part:
        return False
    blocks = unsplittable(text)
    start = text.find(part)
    while start != -1:
        end = start + len(part)
        mid_word = start > 0 and text[start - 1].isalpha() and text[start].isalpha()
        if not mid_word and not any(a < start < b or a < end < b for a, b in blocks):
            return True
        start = text.find(part, start + 1)
    return False


def partial_quote(raw, value):
    """선택 값이 원문 전체가 아니라 그 일부인가(출력에 '원문 일부'라고 밝힌다)."""
    return isinstance(raw, str) and isinstance(value, str) and _norm(raw) != _norm(value)


def from_label(raw, e):
    """선택 값이 그 칸 원문이 아니라 그 칸의 행·열 라벨(전체 또는 이어진 일부)인가.

    답이 라벨에 적혀 있는 질문이 있다(예: 행 라벨 '1년(3개월 회전)'에서 회전기간). 라벨도 원문이므로 값으로 인정하되,
    출력에서 칸 값과 구분해 보여 준다. 칸 원문으로도 읽히는 값은 칸 값으로 본다.
    """
    if not isinstance(raw, str) or not raw.strip() or cell_line(raw, e.get("value_raw")):
        return False
    return any(cell_line(raw, label) for label in (e.get("row_path") or []) + (e.get("column_path") or []) if label)


LONG_VALUE = 30     # 이보다 긴 값(글자 수, 공백 제외)은 문장에 통째로 옮기지 않아도 된다
SHARED_PIECE = 6    # 긴 값과 문장이 함께 가져야 하는 이어진 글자 수(공백 제외)


def said(value, text):
    """답변 문장이 이 값을 담고 있는가.

    짧은 값(숫자, 짧은 구절)은 문장에 그대로 있어야 한다. 긴 글 칸(약관 조문, 설명문)을 근거로 고른 경우에는 문장이 그 글을
    통째로 옮길 필요가 없다. 그 글의 숫자 하나, 또는 이어진 SHARED_PIECE자 이상이 문장에 있으면 그 글을 근거로 쓴 문장으로
    본다. 문장에 근거에 없는 숫자가 있으면 따로 걸러진다(UNSUPPORTED_NUMBER_IN_TEXT)."""
    from doc_understanding.rag.generation.narrative import numbers

    v, t = _norm(value), _norm(text)
    if v in t:
        return True
    if len(v) <= LONG_VALUE:
        return False
    if numbers(value) & numbers(text):
        return True
    return any(v[i:i + SHARED_PIECE] in t for i in range(len(v) - SHARED_PIECE + 1))


def present(parsed, selection, evidence, question="", required_values=None):
    """모델의 답변 문장·다른 해석을 검사한다. 실패하면 근거로 만든 템플릿 문장을 쓴다.
    required_values: 문장에 반드시 나와야 하는 값. 기본은 선택한 값 전부다. 판단 근거라서 인용에만 더한 근거의 원문은
    문장에 없어도 되므로, 호출자가 모델이 직접 고른 값만 넘긴다."""
    from doc_understanding.rag.generation.narrative import numbers

    index = {e["evidence_id"]: e for e in evidence}
    chosen = [index[i] for i in selection["evidence_ids"]]
    # 문장에 쓸 수 있는 숫자: 선택한 근거의 값·경로와, 질문을 되풀이하는 데 필요한 질문 속 숫자.
    allowed = question + " " + " ".join(
        " ".join([e.get("value_raw") or ""] + e.get("row_path", []) + e.get("column_path", [])) for e in chosen)
    text = (parsed.answer_text or "").strip()
    reasons = []
    if selection["abstained"]:
        text = ""
    else:
        if not text:
            reasons.append("EMPTY_ANSWER_TEXT")
        required = selection["values_raw"] if required_values is None else required_values
        if any(not said(v, text) for v in required if v):
            reasons.append("SELECTED_VALUE_NOT_IN_TEXT")
        # 쉼표 표기만 다른 같은 숫자(8000 / 8,000)는 같은 숫자로 본다. 질문의 숫자를 문장에서 쉼표를 넣어 되풀이하는 일이 잦다.
        plain = lambda found: {n.replace(",", "") for n in found}  # noqa: E731
        if plain(numbers(text)) - plain(numbers(allowed)):
            reasons.append("UNSUPPORTED_NUMBER_IN_TEXT")
    source = "model"
    if reasons or not text:
        source = "template" if not selection["abstained"] else "none"
        text = template_text(selection, evidence)
    alternatives = [dict(interpretation=a.interpretation.strip()[:300], evidence_ids=a.evidence_ids)
                    for a in parsed.alternatives
                    if a.interpretation.strip() and a.evidence_ids and set(a.evidence_ids) <= set(index)][:3]
    return dict(answer_text=text, answer_text_source=source, answer_text_reasons=reasons,
                alternatives=alternatives)


def template_text(selection, evidence):
    if selection["abstained"]:
        return ""
    index = {e["evidence_id"]: e for e in evidence}
    parts = []
    for eid, raw in zip(selection["evidence_ids"], selection["values_raw"]):
        e = index[eid]
        path = " / ".join(" ".join(p.split()) for p in (" > ".join(e.get("row_path", [])),
                                                        " > ".join(e.get("column_path", []))) if p)
        if not e.get('table_id') and e.get('scope_context'):
            path = ' > '.join(' '.join(s['text_raw'].split()) + f" ({s.get('page', e.get('page'))}쪽)"
                              for s in e['scope_context'])
        where = f"{e.get('source_name', '')} {e.get('page')}쪽".strip()
        value = " ".join((raw or "").split())
        # 라벨에서 고른 값은 경로에 이미 들어 있으므로 '경로: 칸 값' 대신 고른 라벨만 적는다.
        parts.append(f"{value} ({where})" if from_label(raw, e) else f"{path + ': ' if path else ''}{value} ({where})")
    return "\n".join(parts)


def check_selection(selection, evidence):
    s = AnswerSelection.model_validate(selection)
    lookup = {e["evidence_id"]: e for e in evidence}
    if len(lookup) != len(evidence) or len(set(s.evidence_ids)) != len(s.evidence_ids):
        raise ValueError("DUPLICATE_EVIDENCE_ID")
    if len(s.evidence_ids) != len(s.values_raw):
        raise ValueError("ANSWER_VALUE_COUNT_MISMATCH")
    if s.abstained != (not s.evidence_ids):
        raise ValueError("INCONSISTENT_ABSTENTION")
    for eid, raw in zip(s.evidence_ids, s.values_raw, strict=True):
        # 값은 셀·본문 원문 전체이거나 그 이어진 일부(cell_line), 또는 그 칸의 행·열 라벨이어야 한다. 새 값을 만들 수 없다.
        if eid not in lookup or not (cell_line(raw, lookup[eid]["value_raw"]) or from_label(raw, lookup[eid])):
            raise ValueError("UNSUPPORTED_ANSWER_VALUE_OR_CITATION")
        if not lookup_eligible(lookup[eid]):
            raise ValueError("UNRESOLVED_LOOKUP_BLOCKED")
    return s.model_dump()


CONTEXT_KINDS = ("parent_text", "note", "unit_context", "table_context", "scope_context")


def share_repeated_context(sources):
    """같은 문맥 원문이 근거 셀마다 반복되면(칸 안에 담긴 표의 셀들이 같은 칸 문장을 가짐) 두 번째부터 원문 대신
    same_text_as(처음 나온 출처 ID)만 적는다. 출처 ID·운반 근거·검증 규칙은 그대로이고 요청 크기만 줄인다."""
    first, out = {}, []
    for s in sources:
        key = (s.get("kind"), s.get("text_raw"))
        if s.get("kind") in CONTEXT_KINDS and key in first:
            out.append(dict(source_id=s["source_id"], carrier_evidence_id=s["carrier_evidence_id"], kind=s["kind"],
                            same_text_as=first[key]))
            continue
        first.setdefault(key, s["source_id"])
        out.append(s)
    return out


def drop_context_selections(selection, catalog, evidence, assessment=None):
    """답변 값 목록에 들어온 문맥 출처(적용 범위·부모 문맥·주석 등)를 덜어낸다.

    원문 전체가 같고 운반 근거가 이미 선택됐거나, 별도 답 근거와 이 문맥의 정확한 판단 인용이 있어야 한다.
    후자의 운반 근거는 include_assessed에서 연결하고 validate_assessment에서 다시 검사한다.
    새 값을 만들거나 다른 항목을 바꾸지 않으며, 뺀 항목은 호출자가 기록한다.
    """
    carriers = {e["evidence_id"] for e in evidence}
    sources = {s["source_id"]: s for s in catalog if s["source_id"] not in carriers}
    chosen = set(selection["evidence_ids"]) & carriers
    assessed = {(s["source_id"], s["quote"]) for r in (assessment or {}).get("requirements", [])
                for s in r["supports"] if s["field"] == "text_raw"}
    keep_ids, keep_values, dropped = [], [], []
    if len(selection["evidence_ids"]) != len(selection["values_raw"]):
        return selection, []
    for eid, raw in zip(selection["evidence_ids"], selection["values_raw"]):
        source = sources.get(eid)
        if (source and chosen and raw == source["text_raw"] and
                (source["carrier_evidence_id"] in chosen or (eid, raw) in assessed)):
            dropped.append(dict(source_id=eid, kind=source["kind"], carrier_evidence_id=source["carrier_evidence_id"]))
            continue
        keep_ids.append(eid)
        keep_values.append(raw)
    return dict(selection, evidence_ids=keep_ids, values_raw=keep_values), dropped


def include_assessed(selection, assessment, catalog, evidence):
    """판단에 쓴 출처의 근거가 인용 목록에 없으면 인용에 넣는다. 반환: (선택, 더한 근거 ID 목록).

    모델은 '어느 문서·항목인지 확인한 근거'를 판단에는 쓰고 답변 값으로는 고르지 않는 일이 잦다. 예전에는 그러면 답 전체를
    버렸다. 판단에 쓴 근거는 사용자가 확인할 수 있어야 하므로 버리지 않고 인용으로 드러낸다. 인용 문구가 그 출처 원문에
    있는지, 조회 가능한 근거인지 등 나머지 검사는 그대로다(validate_assessment). 보류를 답으로 바꾸지 않는다.
    """
    if selection["abstained"] or not selection["evidence_ids"]:
        return selection, []
    sources = {s["source_id"]: s for s in catalog}
    lookup = {e["evidence_id"]: e for e in evidence}
    ids, values, added = list(selection["evidence_ids"]), list(selection["values_raw"]), []
    for requirement in assessment.get("requirements") or []:
        for support in requirement.get("supports") or []:
            source = sources.get(support.get("source_id"))
            carrier = source["carrier_evidence_id"] if source else None
            item = lookup.get(carrier)
            if item is None or carrier in ids or not lookup_eligible(item) or not item.get("value_raw"):
                continue
            ids.append(carrier)
            values.append(item["value_raw"])
            added.append(carrier)
    return dict(selection, evidence_ids=ids, values_raw=values), added


def render_answer(selection, evidence):
    s = check_selection(selection, evidence)
    if s["abstained"]:
        return "조회한 근거만으로 질문에 답할 수 없습니다. 요청한 항목·기간·의미 관계를 추가 확인해야 합니다."
    index = {e["evidence_id"]: e for e in evidence}
    lines = ["다음은 원문 추출 결과입니다. 행·열 계층과 단위·주석 범위는 미검증 후보입니다."]
    shown_parents = set()
    for eid, selected in zip(s["evidence_ids"], s["values_raw"]):
        e = index[eid]
        row = " → ".join(e["row_path"]).replace("\n", "")
        col = " → ".join(e["column_path"]).replace("\n", " ")
        raw = (
            "[원본 공란]"
            if e["value_raw"] == ""
            else "[읽기 불가]"
            if e["value_raw"] is None
            else selected if selected != e["value_raw"] else e["value_raw"]
        )
        if from_label(selected, e):
            raw = f"{selected} (이 칸의 행·열 라벨에서 인용. 칸 값: {' '.join(e['value_raw'].split())[:80]})"
        elif e["value_raw"] and partial_quote(selected, e["value_raw"]):
            # 일부만 고른 값은 앞뒤 조건이 빠질 수 있으므로 일부임을 밝힌다. 전체 원문은 근거 목록에 있다.
            raw = f"{selected} (원문 일부)"
        location = e["source_name"] + (
            ", HTML DOM 원문" if e.get("source_media_type") == "text/html" else f", p.{e['page']}"
        )
        if e.get("table_id"):
            location += f", 표 {e['table_id']}, 셀 {e['cell_id']}"
        lines.append(f"- {row + ' / ' + col if row else '본문'}: {raw} [{eid}: {location}]")
        if e.get("source_uri"):
            lines.append("  원문 출처: " + e["source_uri"])
        for other in e.get("group_values") or []:
            lines.append("  같은 항목의 나란한 칸(한 값 묶음): " + " ".join(other["text_raw"].split()))
        for scope in e.get("scope_context", []):
            lines.append("  적용 범위(구조 검토 후보): " + scope["text_raw"])
        if e.get("structure_review"):
            lines.append("  행·열 경로: 구조 검토 후보로 확정(사람 승인 전)")
        for unit in e.get("unit_context", []):
            lines.append("  원문 단위 문맥(적용 후보): " + unit["text_raw"])
        for note in e.get("notes", []):
            lines.append(f"  주석({note['scope']}, {note['status']}): {note['text_raw']}")
        if e.get("table_context") and not e.get("unit_context"):
            lines.append("  원문 표 머리말(단위·기간 적용 후보): " + " / ".join(e["table_context"]))
        context = e.get("nested_context")
        if context and context["parent_cell_id"] not in shown_parents:
            shown_parents.add(context["parent_cell_id"])
            lines.append(
                f"  같은 부모 셀의 원문 문맥(적용 범위 미검증): [{e['source_name']}, p.{context['page']}, 부모 셀 {context['parent_cell_id']}]"
            )
            if context.get("path"):
                lines.append("  담긴 위치(담은 칸 라벨, 바깥 → 안쪽): " + " › ".join(p["label"] for p in context["path"]))
            for p in context.get("outer_prose", []) + context["prose"]:
                lines.append(f"  · {p['text_raw']} [본문 {p['content_id']}]")
    return "\n".join(lines)


def generate(
    question,
    evidence,
    output,
    *,
    model="gpt-4.1",
    allow_external=False,
    max_output_tokens=2400,
    max_input_bytes=60000,
    max_cost_usd=0.15,
    transport=http_transport,
    answer_mode="selection",
    input_usd_per_million=None,
    output_usd_per_million=None,
    presentation=False,
    temperature=0.0,
    comparison_quotes=(),
    answer_request=None,
):
    # temperature: 같은 질문·같은 근거에는 같은 답이 나오도록 0으로 둔다(계획·재정렬 호출과 같은 기준). None이면 보내지 않는다.
    # presentation=True: 선택형 응답에 짧은 답변 문장과 다른 해석을 함께 받는다(화면용, 추가 호출 없음).
    if answer_mode not in ("selection", "narrative"):
        raise ValueError("INVALID_ANSWER_MODE")
    if not allow_external:
        raise ValueError("EXTERNAL_CALL_NOT_ENABLED")
    prices = rates(model, input_usd_per_million, output_usd_per_million)
    import math

    if not math.isfinite(max_cost_usd) or max_cost_usd <= 0:
        raise ValueError("INVALID_MODEL_BUDGET")
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ValueError("API_KEY_UNAVAILABLE")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    # 표·본문·주석을 같은 출처 목록으로 전달한다. 질문 유형별 키워드 분기는 없다.
    from doc_understanding.rag.generation.narrative import (
        SYSTEM as NARRATIVE_SYSTEM,
        NarrativeAnswer,
        build_catalog,
        requirements,
    )

    catalog = build_catalog(evidence)
    # 출처마다 어느 문서인지 짧은 표시(doc)를 붙이고, 문서 이름표(파일 이름 + 첫 쪽 제목)는 한 번만 싣는다.
    # 파일 이름만으로는 문서를 구분할 수 없는 경우가 많다(올린 문서는 모두 input.pdf). 이름표가 없으면 모델은 다른 기관·상품
    # 문서의 값을 질문이 가리킨 대상의 값으로 답한다.
    documents, marks = {}, {}
    for s in catalog:
        ident = (s.get("document_id"), s.get("document_label") or s.get("source_name"))
        if ident not in marks:
            marks[ident] = f"D{len(marks) + 1}"
            documents[marks[ident]] = ident[1] or ""
        s["doc"] = marks[ident]
    compact = [
        {
            k: s[k]
            for k in (
                "source_id",
                "carrier_evidence_id",
                "doc",
                "page",
                "kind",
                "text_raw",
                "row_path",
                "column_path",
                "lookup_eligible",
            )
            if k in s
        }
        for s in catalog
    ]
    compact = share_repeated_context(compact)
    # Keep the list item and its heading visibly linked in the model input. The
    # separate source remains the citation target, with its own original refs.
    scopes = {}
    for source in catalog:
        if source['kind'] == 'scope_context':
            scopes.setdefault(source['carrier_evidence_id'], []).append(
                dict(source_id=source['source_id'],text_raw=source['text_raw']))
    for source in compact:
        if source.get('kind') == 'body' and source['source_id'] in scopes:
            source['scope'] = scopes[source['source_id']]
    presenting = presentation and answer_mode == "selection"
    schema = (NarrativeAnswer if answer_mode == "narrative"
              else PresentedSelection if presenting else GroundedSelection)
    response_schema = schema.model_json_schema()
    if answer_mode == "selection" and evidence:
        response_schema["properties"]["evidence_ids"]["items"]["enum"] = [e["evidence_id"] for e in evidence]
    if answer_mode == 'selection' and answer_request and answer_request.get('kind') == 'name':
        # Naming questions often hit unspaced definition lines. Select complete
        # original values/labels; never relax the word-boundary citation check.
        values = [s['text_raw'] for s in compact if s.get('kind') in ('body', 'table_cell') and s.get('text_raw')]
        values += [label for s in compact for label in s.get('row_path', []) + s.get('column_path', [])]
        response_schema['properties']['values_raw']['items']['enum'] = [None, *dict.fromkeys(' '.join(v.split()) for v in values)]
    instruction = NARRATIVE_SYSTEM if answer_mode == "narrative" else SYSTEM
    if presenting:
        instruction += PRESENTATION_INSTRUCTIONS
    if comparison_quotes:
        # Comparisons can share a condition in the question. Keep each assessment anchored
        # even when the model would otherwise join the subject and condition with an ellipsis.
        request_quotes = [answer_request['question_quote']] if answer_request else []
        quotes = list(dict.fromkeys(s for s in [*comparison_quotes, *request_quotes, question]
                                   if s and s in question and len(s) <= 4000))
        if quotes:
            response_schema["$defs"]["Requirement"]["properties"]["question_quote"]["enum"] = quotes
            instruction += COMPARISON_INSTRUCTIONS
    if answer_mode == "selection" and answer_request:
        from copy import deepcopy

        primary = deepcopy(response_schema["$defs"]["Requirement"])
        for field in ("question_quote", "requirement"):
            primary["properties"][field]["enum"] = [answer_request[field]]
        assessment_schema = response_schema["$defs"]["QuestionAssessment"]
        assessment_schema["properties"]["requested_answer"] = primary
        assessment_schema["required"].append("requested_answer")
        assessment_schema["properties"]["requirements"]["minItems"] = 0
        assessment_schema["properties"]["requirements"]["maxItems"] -= 1
        instruction += ("\nJudge answer_request in assessment.requested_answer with its own status and exact supports. "
                        "Use requirements for additional conditions only. Copy each additional question_quote "
                        "verbatim from the question. If there are no additional conditions, return an empty list.")
    payload = dict(
        model=model,
        store=False,
        max_output_tokens=max_output_tokens,
        input=[
            dict(role="developer", content=instruction),
            dict(
                role="user",
                content=json.dumps(
                    dict(question=question, documents=documents, sources=compact,
                         **({'answer_request': answer_request} if answer_request is not None else {}),
                         **requirements(question, catalog)),
                    ensure_ascii=False,
                ),
            ),
        ],
        text=dict(
            format=dict(
                type="json_schema",
                name="grounded_sentence_answer"
                if answer_mode == "narrative"
                else "grounded_answer_selection",
                strict=True,
                schema=response_schema,
            )
        ),
    )
    if temperature is not None and not model.strip().lower().startswith(NO_TEMPERATURE_PREFIXES):
        payload["temperature"] = temperature  # 추론 모델 계열은 이 설정을 거부하므로 보내지 않는다.
    raw_request = json.dumps(payload, ensure_ascii=False).encode("utf8")
    # UTF-8 byte bound intentionally overestimates tokens. No hidden retries/tools.
    reserve = cost(len(raw_request), payload["max_output_tokens"], prices)
    if len(raw_request) > max_input_bytes or reserve > max_cost_usd:
        raise ValueError("MODEL_REQUEST_BUDGET_EXCEEDED")

    def write(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf8")

    write("request.json", payload)
    write("evidence.json", evidence)
    if catalog is not None:
        write("source-catalog.json", catalog)
    started = perf_counter()
    try:
        response = transport(payload, key, 45)
        write("response.json", response)
        if response.get("status") != "completed":
            raise ValueError("INCOMPLETE_MODEL_RESPONSE")
        content = [
            c
            for item in response.get("output", [])
            if item.get("type") == "message"
            for c in item.get("content", [])
        ]
        if any(c.get("type") == "refusal" for c in content):
            raise ValueError("MODEL_REFUSAL")
        text = "".join(c.get("text", "") for c in content if c.get("type") == "output_text")
        draft = json.loads(text)
        if answer_mode == "selection" and answer_request and "requested_answer" in draft.get("assessment", {}):
            assessment = draft["assessment"]
            assessment["requirements"] = [assessment.pop("requested_answer"), *assessment["requirements"]]
        narrative = None
        if answer_mode == "narrative":
            from doc_understanding.rag.generation.narrative import validate_render
            from doc_understanding.runtime.telemetry import span

            write("draft.json", draft)
            with span("answer.validate_sentences", {"question": question}) as record:
                narrative = validate_render(draft, catalog, question, evidence, answer_request=answer_request)
                record["output"] = narrative
            write("sentence-validation.json", narrative)
            selection = narrative["selection"]
            assessment_validation = narrative["assessment_validation"]
        else:
            parsed = schema.model_validate(draft)
            raw_selection, dropped = drop_context_selections(
                parsed.model_dump(include={"evidence_ids", "values_raw", "abstained"}), catalog, evidence,
                parsed.assessment.model_dump())
            if dropped:
                write("dropped-context-selections.json", dropped)
            if raw_selection["abstained"] and raw_selection["evidence_ids"]:
                # 모델이 보류를 택했으면 보류가 우선이다. 함께 낸 선택은 답으로 쓰지 않고 기록만 남긴다.
                write("ignored-selection-on-abstention.json", raw_selection)
                raw_selection = dict(raw_selection, evidence_ids=[], values_raw=[])
            rejected = None
            try:
                selection = check_selection(raw_selection, evidence)
            except ValueError as error:
                if str(error) not in {"UNSUPPORTED_ANSWER_VALUE_OR_CITATION", "UNRESOLVED_LOOKUP_BLOCKED",
                                     "ANSWER_VALUE_COUNT_MISMATCH", "DUPLICATE_EVIDENCE_ID", "INCONSISTENT_ABSTENTION"}:
                    raise
                rejected = str(error)
                selection = dict(evidence_ids=[], values_raw=[], abstained=True)
            own_values = list(selection["values_raw"])  # 모델이 답으로 직접 고른 값(답변 문장에 나와야 하는 값)
            selection, assessed = include_assessed(selection, parsed.assessment.model_dump(), catalog, evidence)
            if assessed:
                write("assessed-evidence-added-to-citations.json", assessed)
            assessment_validation = validate_assessment(
                parsed.assessment.model_dump(), question, catalog, selection["evidence_ids"],
                answer_request=answer_request,
            )
            if rejected:
                assessment_validation["passed"] = False
                assessment_validation["reasons"].append(rejected)
            # 근거 판단 또는 인용 연결이 불충분하면 모델의 선택을 답변으로 내보내지 않는다.
            if not assessment_validation["passed"]:
                selection = dict(evidence_ids=[], values_raw=[], abstained=True)
            if presenting:
                presented = present(parsed, selection, evidence, question, required_values=own_values)
                if rejected:
                    presented["alternatives"] = []
        selected_ids = set(selection["evidence_ids"])
        chosen = [e for e in evidence if e["evidence_id"] in selected_ids]
        conflict = period_conflict(question, chosen, evidence) if chosen else None
        if conflict:
            assessment_validation["passed"] = False
            assessment_validation["reasons"].append(conflict)
            selection = dict(evidence_ids=[], values_raw=[], abstained=True)
            narrative = None
            if presenting:
                presented = dict(answer_text="", answer_text_source="none",
                                 answer_text_reasons=[conflict], alternatives=[])
        write("assessment-validation.json", assessment_validation)
        usage = response.get("usage") or {}
        estimated = cost(usage.get("input_tokens", 0), usage.get("output_tokens", 0), prices)
        result = dict(
            question=question,
            answer=narrative["answer"] if narrative else render_answer(selection, evidence),
            selection=selection,
            model=response.get("model", model),
            usage=usage,
            estimated_cost_usd=estimated,
            reserved_cost_usd=reserve,
            pricing_basis=prices[2],
            input_usd_per_million=prices[0],
            output_usd_per_million=prices[1],
            request_sha256=hashlib.sha256(raw_request).hexdigest(),
            generation_mode="llm_evidence_selection_with_deterministic_rendering",
            semantic_status="unvalidated_candidate",
            facts_published=0,
            model_calls=1,
            assessment_validation=assessment_validation,
            elapsed_seconds=perf_counter() - started,
        )
        if narrative:
            result.update(narrative)
        if presenting and answer_mode == "selection":
            result.update(presented)
        write("answer.json", result)
        return result
    except Exception as error:
        write(
            "failure.json",
            dict(
                error=type(error).__name__,
                # 우리 검사 코드(대문자 코드)만 남긴다. 모델 출력이나 다른 예외 문구는 적지 않는다.
                reason=str(error) if isinstance(error, ValueError) and re.fullmatch(r"[A-Z_]+", str(error)) else None,
                attempts=1,
                elapsed_seconds=perf_counter() - started,
                reserved_cost_usd=reserve,
                facts_published=0,
            ),
        )
        raise RuntimeError("ANSWER_CALL_FAILED_SEE_LOCAL_AUDIT") from None
