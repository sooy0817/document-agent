# 역할: 중첩 표가 같은 부모 셀 안의 어느 소제목 아래에 있는지(적용 범위)를 읽기 순서와 형태로 판정한다.
"""Scope candidates (ADR-078). 단어 사전 없이 줄의 형태만 본다.

- 대상: 부모 셀 연결이 linked/complete/total인 자식 표
- 후보: 같은 부모 셀에서 이전 자식 표(또는 시작) 이후, 이 표 직전까지의 제목 형태 줄
- 표 뒤에 오는 제목은 후보가 아니다. 후보 1개면 결정, 여러 개면 보류, 없으면 해당 없음
"""
import re

from doc_understanding.agents.structure_review.models import Decision
from doc_understanding.parser.assembly.nested_content import build_nested_content

BRACKETED = re.compile(r"^\s*[\[［【〔<〈《][^\]］】〕>〉》\n]{1,80}[\]］】〕>〉》]\s*$")
BULLET = re.compile(r"^\s*[-–•▪◦·※○●□■◆◇▶►]")
SENTENCE_END = re.compile(r"(다|요|음|함|됨)\s*[.。]?\s*$|[.。:;：]\s*$")


def heading_like(text, following=None, previous=None):
    """괄호로 감싼 짧은 줄, 또는 문장 끝이 아닌 짧은 줄 다음에 글머리 기호 줄이 오면 제목 형태다.

    괄호 안이 "항목: 값" 형태이면 메타 표기로 보고 제외한다. 바로 앞 줄이 문장 중간에서 끝났으면
    줄바꿈으로 이어진 문장의 끝부분이므로 제외한다.
    """
    text = (text or "").strip()
    if not text or len(text) > 80:
        return False
    if BRACKETED.match(text):
        return not re.search(r"[:：]", text)
    previous = (previous or "").strip()
    continues = bool(previous) and not (SENTENCE_END.search(previous) or BRACKETED.match(previous))
    return (len(re.sub(r"\s+", "", text)) <= 30 and not BULLET.match(text) and not continues
            and not SENTENCE_END.search(text) and bool(following and BULLET.match(following)))


def scope_candidates(items, texts, table_id):
    """items: 부모 셀 안의 읽기 순서 [(kind, ref_id)], texts: content_id → 원문."""
    position = next(i for i, (kind, ref) in enumerate(items) if kind == "table" and ref == table_id)
    start = max((i for i, (kind, _) in enumerate(items[:position]) if kind != "content"), default=-1) + 1
    segment = [ref for kind, ref in items[start:position] if kind == "content"]
    found = []
    for i, ref in enumerate(segment):
        following = texts.get(segment[i + 1]) if i + 1 < len(segment) else None
        previous = texts.get(segment[i - 1]) if i > 0 else None
        if heading_like(texts.get(ref), following, previous):
            found.append(dict(content_id=ref, text_raw=texts.get(ref, "")))
    return found


def decide_scopes(doc):
    """문서의 중첩 자식 표마다 적용 범위 판정을 반환한다. 문서는 수정하지 않는다."""
    texts = {c.content_id: c.text_raw for c in doc.contents}
    decisions = []
    for link in build_nested_content(doc).cells:
        if not link.child_table_ids:
            continue
        ready = (link.containment_status == "linked" and link.coverage_status == "complete"
                 and link.reading_order_status == "total")
        items = [(i.kind, i.ref_id) for i in link.items]
        for table_id in link.child_table_ids:
            common = dict(table_id=table_id, aspect="scope")
            if not ready or ("table", table_id) not in items:
                decisions.append(Decision(status="not_applicable", reason="READING_ORDER_NOT_TOTAL", **common))
                continue
            found = scope_candidates(items, texts, table_id)
            ids = tuple(f["content_id"] for f in found)
            if len(found) == 1:
                decisions.append(Decision(status="decided", reason="SINGLE_PRECEDING_HEADING",
                                          choice=found[0], evidence_ids=ids, candidates=tuple(found),
                                          supports=("PRECEDING_HEADING_IN_PARENT_CELL",), **common))
            elif found:
                decisions.append(Decision(status="ambiguous", reason="MULTIPLE_HEADINGS",
                                          evidence_ids=ids, candidates=tuple(found), **common))
            else:
                decisions.append(Decision(status="not_applicable", reason="NO_HEADING_BEFORE_TABLE", **common))
    return decisions
