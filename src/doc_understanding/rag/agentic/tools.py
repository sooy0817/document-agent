# 역할: 질문 이해 에이전트의 결정적 읽기 전용 도구. 표 구조 조회·구조 일치·적용 범위 확인. 값을 만들지 않는다.
"""Deterministic tools (ADR-079).

구조 일치는 요구 사항 문구와 표의 행·열 라벨을 공백을 무시하고 비교한다. 동의어나 의미는 판단하지 않는다.
답변 가능 여부(lookup_eligible)는 기존 근거 복원 규칙을 그대로 따른다.
"""
import re

from doc_understanding.rag.evidence.cells import display_path
from doc_understanding.rag.evidence.source import as_source


def norm(text):
    return re.sub(r"\s+", "", text or "").lower()


def _matches(term, label):
    t, s = norm(term), norm(label)
    return len(t) >= 2 and bool(s) and (t in s or (len(s) >= 2 and s in t))


def table_outline(doc, table_id):
    """표의 적용 범위와 행·열 경로 목록(중복 제거). 추적 기록과 검토 표시용이다. doc는 문서나 근거 출처다."""
    table = as_source(doc).table(table_id)
    cells = table["cells"]
    return dict(table_id=table_id, page=table["page"],
                scope=table["scope"],
                # 글자를 읽지 못한 라벨(None)이 있는 표도 목록은 만든다(스캔 문서 등).
                rows=sorted({display_path(c["row_path"]) for c in cells if c["row_path"]}),
                columns=sorted({display_path(c["column_path"]) for c in cells if c["column_path"]}),
                eligible=sum(c["lookup_eligible"] for c in cells), values=len(cells))


def scope_match(cell, scope_terms):
    """적용 범위 문구가 검토 후보 범위·표 머리말·부모 셀 문맥에 나타나는지. 없으면 None(판단 불가)."""
    if not scope_terms:
        return None
    texts = [s["text_raw"] for s in cell.get("scope_context") or []]
    texts += cell.get("table_context") or []
    context = cell.get("nested_context") or {}
    if not texts:
        # 다른 범위 정보가 없을 때만 칸 안 문장을 본다(표 뒤 문장이 다른 범위를 말할 수 있다).
        texts += [p["text_raw"] for p in context.get("outer_prose", []) + context.get("prose", [])]
    # 칸 안에 담긴 표: 담은 칸 라벨 경로는 그 표가 놓인 위치라 함께 본다.
    texts += [p["label"] for p in context.get("path", [])]
    return any(_matches(term, text) for term in scope_terms for text in texts)


def structure_matches(doc, table_id, item_terms, scope_terms=(), similarity=None):
    """요구 문구를 답변 가능 셀의 행·열 라벨과 의미 층별로 비교해 점수순으로 반환한다(matching.py).

    문자 일치 +1, 숫자 구간 포함 +2, 같은 차원의 구간 배제 -2, 임베딩 유사도 +0.5~1.0, 적용 범위 ±1.
    양의 일치가 하나도 없는 셀은 반환하지 않는다. 미확정 셀은 반환하지 않는다.
    """
    from doc_understanding.rag.agentic.matching import match_term

    out = []
    for cell in as_source(doc).cells(table_id):
        if not cell["lookup_eligible"]:
            continue
        labels = cell["row_path"] + cell["column_path"]
        matches = [m for t in item_terms for m in [match_term(t, labels, similarity)] if m]
        positive = [m for m in matches if m["weight"] > 0]
        if not positive:
            continue
        scoped = scope_match(cell, scope_terms)
        score = sum(m["weight"] for m in matches) + (1 if scoped else 0) - (1 if scoped is False else 0)
        out.append(dict(cell, matched_terms=sorted({m["term"] for m in positive}), match_details=matches,
                        scope_matched=scoped, match_score=round(score, 3)))
    return sorted(out, key=lambda c: -c["match_score"])
