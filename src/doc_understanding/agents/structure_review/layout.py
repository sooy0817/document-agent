# 역할: 1행 헤더·1열 라벨 가설의 원문 근거를 강함·보통·반대 근거로 등급화하고 결정적으로 판정한다.
"""Layout evidence grading (ADR-078). 형태 근거만 사용하며 특정 문서의 단어나 정답을 쓰지 않는다.

규칙 분석기의 기준선(header_evidence.py)보다 공격적일 수 있다. 결과는 별도 검토 후보이며
평가셋에서 "결정" 정확도를 재서 보정한다.
"""
import re
from statistics import median

from doc_understanding.agents.structure_review.models import Decision

LAYOUT = {"header_rows": 1, "stub_columns": 1}


def _norm(text):
    return re.sub(r"\s+", "", text or "")


def _area(box):
    return max(0, box.x1 - box.x0) * max(0, box.y1 - box.y0)


def _overlap(a, b):
    return max(0, min(a.x1, b.x1) - max(a.x0, b.x0)) * max(0, min(a.y1, b.y1) - max(a.y0, b.y0))


def _rectangular(block):
    commands, bb = block.drawing_commands, block.source.bbox
    if len(commands) == 1 and commands[0].operator == "re":
        return True
    if len(commands) == 4 and all(c.operator == "l" and len(c.coordinates) == 4 for c in commands):
        corners = {(bb.x0, bb.y0), (bb.x1, bb.y0), (bb.x0, bb.y1), (bb.x1, bb.y1)}
        edges = [(tuple(c.coordinates[:2]), tuple(c.coordinates[2:])) for c in commands]
        return (all(a in corners and z in corners and a != z and (a[0] == z[0] or a[1] == z[1])
                    for a, z in edges) and len({frozenset(e) for e in edges}) == 4)
    return False


def cell_fills(page, table, cell):
    """셀을 충분히 덮는 직사각형 채움 블록. 임의 도형의 외접 상자는 근거로 쓰지 않는다."""
    out = []
    for b in page.blocks:
        if b.kind != "drawing" or b.drawing_stroked is not False or not _rectangular(b):
            continue
        bb = b.source.bbox
        if (b.source.document_id == table.document_id and b.source.page_number == table.page_number
                and _area(bb) <= 1.5 * _area(cell.source_bbox)
                and _overlap(bb, cell.source_bbox) >= 0.85 * _area(cell.source_bbox)):
            out.append(b)
    return out


def cell_spans(page, table, cell):
    spans = {s.span_id: s for b in page.blocks for s in b.spans}
    return [spans[s] for s in cell.span_refs if s in spans and spans[s].text_raw.strip()
            and spans[s].source.page_number == table.page_number]


def grade(table, page):
    """판정에 쓰는 근거 목록과 대상 셀을 반환한다. 표·원문은 수정하지 않는다."""
    rows = {c.row_index for c in table.cells if c.row_index is not None}
    cols = {c.column_index for c in table.cells if c.column_index is not None}
    heads = sorted((c for c in table.cells if c.row_index == 0), key=lambda c: c.column_index or 0)
    body = [c for c in table.cells if c.row_index is not None and c.row_index > 0]
    values = [c for c in body if c.column_index is not None and c.column_index > 0]
    out = dict(rows=len(rows), columns=len(cols), head_ids=[c.cell_id for c in heads],
               value_ids=[c.cell_id for c in values], supports=[], counters=[], fill_ids=[])
    if len(rows) < 2 or len(cols) < 2 or not values:
        out["not_applicable"] = "NO_BODY_ROWS" if len(rows) < 2 or not values else "SINGLE_COLUMN"
        return out
    labels = [_norm(c.original_text) for c in heads]
    texts = [_norm(c.original_text) for c in values]
    # 반대 근거
    if any(c.status == "unresolved" for c in heads + body):
        out["counters"].append("UNRESOLVED_PHYSICAL_CELL")
    # 첫 행 칸이 아래 행까지 내려오면(행 병합) '머리글 1행' 해석과 부딪힌다. 여러 열에 걸친 머리글(열 병합)은
    # 반대 근거가 아니다: 걸친 열들의 공통 머리글이며, 라벨 열에 걸친 경우는 행별 라벨 깊이 규칙이 다룬다.
    if any((c.rowspan or 1) > 1 for c in heads):
        out["counters"].append("MERGED_HEADER_ROW")
    if any(t and not re.search(r"[A-Za-z가-힣]", t) for t in labels) or not all(labels):
        out["counters"].append("HEADER_LABEL_NOT_TEXTUAL")
    if len(set(labels)) != len(labels):
        out["counters"].append("DUPLICATE_HEADER_LABELS")
    head_fills = [cell_fills(page, table, c) for c in heads]
    value_fills = [cell_fills(page, table, c) for c in values]
    if any(value_fills):
        out["counters"].append("VALUE_CELLS_FILLED")
    # 강한 근거
    if all(head_fills) and not any(value_fills):
        out["supports"].append("HEADER_FILL_BAND")
        out["fill_ids"] = [b.block_id for f in head_fills for b in f]
    head_spans = [cell_spans(page, table, c) for c in heads]
    value_spans = [cell_spans(page, table, c) for c in values]
    if (all(head_spans) and all(value_spans)
            and all(all(s.flags & 16 for s in ss) for ss in head_spans)
            and not any(s.flags & 16 for ss in value_spans for s in ss)):
        out["supports"].append("HEADER_BOLD_BAND")
    # 글자 머리글 아래 숫자만 있는 열들: 첫 행만 글자이고 본문 값이 거의 모두 숫자면 첫 행은 머리글이다(언어 무관).
    # 작은 표에서는 우연일 수 있어 본문 3행·숫자 값 6개 이상일 때만 강한 근거로 본다.
    filled = [t for t in texts if t]
    numeric = [t for t in filled if NUMERIC.fullmatch(t)]
    if (all(labels) and all(any(ch.isalpha() for ch in t) for t in labels) and len(rows) >= 4
            and len(numeric) >= 6 and len(numeric) >= 0.8 * len(filled)):
        out["supports"].append("NUMERIC_BODY")
    # 보통 근거
    if all(labels) and not any(re.search(r"\d", t) for t in labels) and \
            sum(bool(re.search(r"\d", t)) for t in texts) >= len(texts) / 2:
        out["supports"].append("DIGIT_CONTRAST")
    if labels and texts and median(map(len, texts)) >= max(12, 2 * median(map(len, labels))):
        out["supports"].append("LENGTH_CONTRAST")
    # 키-값 형태: 모든 행에서 왼쪽 열은 짧고 오른쪽 열은 긴 설명. 첫 행도 같은 형태이면 헤더가 없을 수 있다.
    if len(cols) == 2:
        left = [len(_norm(c.original_text)) for c in table.cells if c.column_index == 0]
        right = [len(_norm(c.original_text)) for c in table.cells if c.column_index == 1]
        out["key_value_shape"] = bool(left and right and median(right) >= max(12, 2 * median(left))
                                      and len(_norm(heads[-1].original_text)) >= max(12, 2 * len(labels[0])))
    return out


STRONG = {"HEADER_FILL_BAND", "HEADER_BOLD_BAND", "NUMERIC_BODY"}
# 숫자만 있는 값(부호·천 단위·소수·비율·괄호·증감 기호 포함). 글자가 하나라도 있으면 숫자 값이 아니다.
NUMERIC = re.compile(r"[\d.,+\-−–△▲▽%()]*\d[\d.,+\-−–△▲▽%()]*")
HEADERLESS = {"header_rows": 0, "stub_columns": 1}


def decide_layout(table, page):
    """강함 1개 이상 또는 보통 2개 이상이고 반대 근거가 없으면 1행 헤더·1열 라벨로 결정한다."""
    ev = grade(table, page)
    common = dict(table_id=table.table_id, aspect="layout", supports=tuple(ev["supports"]),
                  counters=tuple(ev["counters"]),
                  evidence_ids=tuple(ev["head_ids"] + ev["value_ids"] + ev["fill_ids"]),
                  candidates=({"header_rows": 1, "stub_columns": 1},))
    if "not_applicable" in ev:
        return Decision(status="not_applicable", reason=ev["not_applicable"], **common)
    strong = STRONG & set(ev["supports"])
    medium = set(ev["supports"]) - STRONG
    enough = bool(strong) or len(medium) >= 2
    if not strong and ev.get("key_value_shape"):
        # 모든 행이 왼쪽 짧은 항목명·오른쪽 긴 설명이고 첫 행도 같은 모양이면 머리글 없는 라벨|내용 표다.
        # 값은 행 경로만 가진다(표 배치 명세의 header_rows=0).
        if ev["counters"]:
            return Decision(status="ambiguous", reason="CONFLICT", **common)
        return Decision(status="decided", reason="KEY_VALUE_ROWS", choice=dict(HEADERLESS),
                        **dict(common, supports=common["supports"] + ("KEY_VALUE_SHAPE",),
                               candidates=({"header_rows": 1, "stub_columns": 1}, dict(HEADERLESS))))
    # 값 칸의 배경색은 '첫 행만 색칠' 근거를 무너뜨릴 뿐이다. 굵은 글씨·숫자 본문 근거와는 부딪히지 않는다.
    if strong - {"HEADER_FILL_BAND"} and "VALUE_CELLS_FILLED" in ev["counters"]:
        ev["counters"] = [c for c in ev["counters"] if c != "VALUE_CELLS_FILLED"]
        common["counters"] = tuple(ev["counters"])
    if enough and not ev["counters"]:
        return Decision(status="decided", reason="SUPPORTED", choice=dict(LAYOUT), **common)
    if ev["counters"] and ev["supports"]:
        return Decision(status="ambiguous", reason="CONFLICT", **common)
    return Decision(status="ambiguous", reason="NO_EVIDENCE" if not ev["supports"] else "INSUFFICIENT_EVIDENCE",
                    **common)
