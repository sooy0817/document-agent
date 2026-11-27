# 역할: 새로 넣은 문서에서 매핑 이전 단계의 문제(글자 없음·표가 아닌 표·조각난 표)를 쪽·표 단위로 기록한다.
"""적재 점검(읽기 전용, 정답표 없이도 쓸 수 있다). CanonicalDocument만 읽는다.

- OCR_REQUIRED: 쪽에 글자 정보가 없다(스캔본). 표 칸은 잡혀도 내용이 없다.
- TABLE_WITHOUT_TEXT: 표의 모든 칸이 비어 있다.
- SPARSE_NUMERIC_TABLE: 칸 대부분이 비고 채워진 칸이 모두 숫자다(그래프 축 눈금을 표로 잡았을 가능성).
- OVERLAPPING_CELLS: 같은 행열 좌표에 칸이 둘 이상이다(라벨과 내용이 겹쳐 잡힌 조각).
- EMPTY_GRID: 행이나 열이 없는 표.
판단은 후보다. 원인 확정이나 수정은 하지 않는다.
"""
import re
from collections import Counter

NUMERIC = re.compile(r"[\d\s.,+\-−–△▲▽%()]*\d[\d\s.,+\-−–△▲▽%()]*")


def document_issues(doc, alias=None):
    name = alias or doc.document_ir.source_name
    out = []
    for page in doc.document_ir.pages:
        if page.text_status != "native":
            out.append(dict(doc=name, kind="OCR_REQUIRED", page=page.page_number, table=None,
                            detail=f"text_status={page.text_status}"))
    for t in doc.physical_tables:
        short = "/".join(t.table_id.split("/")[-2:])
        base = dict(doc=name, page=t.page_number, table=short)
        cells = list(t.cells)
        texts = [(c.original_text or "").strip() for c in cells]
        filled = [x for x in texts if x]
        if not t.rows or not t.columns or not cells:
            out.append(dict(base, kind="EMPTY_GRID", detail=f"{len(t.rows)}x{len(t.columns)} cells={len(cells)}"))
            continue
        if not filled:
            out.append(dict(base, kind="TABLE_WITHOUT_TEXT", detail=f"{len(cells)} cells"))
            continue
        if len(cells) >= 8 and len(filled) <= 0.4 * len(cells) and all(NUMERIC.fullmatch(x) for x in filled):
            out.append(dict(base, kind="SPARSE_NUMERIC_TABLE",
                            detail=f"{len(filled)}/{len(cells)} cells filled, all numeric: {filled[:5]}"))
        slots = Counter((c.row_index, c.column_index) for c in cells if c.row_index is not None)
        if any(n > 1 for n in slots.values()):
            out.append(dict(base, kind="OVERLAPPING_CELLS",
                            detail=" / ".join(" ".join(x.split())[:40] for x in filled[:2])))
    return out


def summarize(issues):
    by_kind, by_doc = Counter(i["kind"] for i in issues), {}
    for i in issues:
        by_doc.setdefault(i["doc"], Counter())[i["kind"]] += 1
    return dict(total=len(issues), by_kind=dict(by_kind), by_doc={d: dict(c) for d, c in by_doc.items()})
