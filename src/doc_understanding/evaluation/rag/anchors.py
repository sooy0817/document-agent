# 역할: 정답 위치(문서·쪽·원문 일부)를 실행 문서의 셀·본문 ID로 바꾼다. 문서는 읽기만 한다.
import re


def norm(text):
    return re.sub(r"\s+", "", text or "")


def row_labelled(table, cell, label):
    """같은 표에서 cell의 행 범위를 덮는 다른 셀 원문에 label이 있는가."""
    def span(c):
        # 행 번호·병합 정보가 없는 칸(물리 좌표 미확정)은 어떤 행과도 겹치지 않는 것으로 본다.
        return set() if c.row_index is None else set(range(c.row_index, c.row_index + (c.rowspan or 1)))
    rows = span(cell)
    return any(other is not cell and rows & span(other) and label in norm(other.original_text)
               for other in table.cells)


def resolve(doc, anchor):
    """anchor.text를 포함하는 같은 쪽의 물리 셀·본문 Content ID 집합. 없으면 빈 집합(해석 실패).

    row_label이 있으면 그 라벨이 같은 행에 있는 표 셀만 남긴다(본문 Content는 제외).
    """
    target = norm(anchor.text)
    tables = [t for t in doc.physical_tables if t.page_number == anchor.page]
    if anchor.row_label:
        label = norm(anchor.row_label)
        return {c.cell_id for t in tables for c in t.cells
                if target and target in norm(c.original_text) and row_labelled(t, c, label)}
    cells = [c for t in tables for c in t.cells]
    # 원문이 정확히 같은 셀이 있으면 그 셀만 정답으로 본다("0.1"이 "0.10"·"최저금리는 0.1"에 걸리지 않게).
    exact = {c.cell_id for c in cells if target and norm(c.original_text) == target}
    if exact:
        return exact
    ids = {c.cell_id for c in cells if target and target in norm(c.original_text)}
    for content in doc.contents:
        refs = content.source_references
        if content.text_raw and refs and refs[0].page_number == anchor.page and target in norm(content.text_raw):
            ids.add(content.content_id)
    return ids


def resolve_item(item, documents, docs_by_id):
    """항목의 정답·대안 정답을 ID로 바꾼다. docs_by_id: document_id → CanonicalDocument."""
    def ids(anchors):
        out, missing, groups = set(), [], []
        for a in anchors:
            doc = docs_by_id.get(documents[a.doc])
            found = resolve(doc, a) if doc else set()
            for equivalent in a.equivalent_locations:
                if equivalent.doc != a.doc:
                    raise ValueError('EQUIVALENT_LOCATION_DIFFERENT_DOCUMENT')
                if doc:
                    found |= resolve(doc,equivalent)
            if not found:
                missing.append(a.model_dump())
            out |= found
            groups.append(found)
        return out, missing, groups
    gold, missing, groups = ids(item.gold)
    alternative, alt_missing, alt_groups = ids(item.alternative_gold)
    return dict(gold=gold, gold_groups=groups, alternative=alternative, alternative_groups=alt_groups, unresolved=missing + alt_missing,
                unresolved_gold=missing, unresolved_alternative=alt_missing)
