# 역할: 셀 안이 하나의 내용(문장·표가 원본 순서로 놓인 작은 문서)인 '내용을 담는 칸'과, 칸 안에 담긴 표의 위치를 찾는다.
"""내용을 담는 칸(container)

파서의 중첩 연결(parser.assembly.nested_content, 기하 연결·의미 미검증)을 물리 표 셀에 대응시킨다.
- 부모 원본 칸을 원천으로 가진 물리 셀 중 안쪽 표 영역 밖에 있는 셀이 담는 칸이다. 여럿이면 가장 안쪽 셀.
- 칸 하나로 좁혀지지 않는 연결(쪽 전체 테두리 등)은 담는 칸으로 보지 않는다.
- 안쪽 표는 담긴 위치(parents: 바깥 → 안쪽 담는 칸과 그 라벨)를 갖는다. 무엇에 적용되는지는 판단하지 않는다.
- duplicates: 칸 원문이 안쪽 표·문장과 같은 글자를 이어 붙인 것이라 근거·검색에서 빼는 칸
  (포함·덮음·읽기 순서가 모두 확정된 칸만. 불완전한 칸은 원문을 남겨 내용이 사라지지 않게 한다).
"""
from doc_understanding.parser.assembly.nested_content import build_nested_content
from doc_understanding.parser.assembly.parent_cells import parent_cell_refs

# 칸 안 문장이 이보다 길면 검색 투영(rag.indexing.nested)이 문맥·문장 조각을 만들지 않는다.
# 그런 칸은 대체 근거가 없으므로 원문을 중복으로 빼지 않는다.
MAX_CONTEXT_CHARS = 6000

_CACHE = {}  # id(문서) → (문서, 결과). 같은 문서 객체를 질의 중 여러 번 볼 때 다시 계산하지 않는다.
_LINKS = {}


def _cached(store, doc, build):
    key = id(doc)
    if key in store and store[key][0] is doc:
        return store[key][1]
    value = build()
    if len(store) >= 8:
        store.pop(next(iter(store)))
    store[key] = (doc, value)
    return value


def nested_links(doc):
    """파서 중첩 연결(build_nested_content)을 문서마다 한 번만 계산한다."""
    return _cached(_LINKS, doc, lambda: build_nested_content(doc))


def containment(doc):
    """담는 칸과 안쪽 표의 담긴 위치. {containers: 셀 ID → 내용, parent_of: 표 ID → 담는 칸 셀 ID}."""
    return _cached(_CACHE, doc, lambda: _containment(doc))


def _containment(doc):
    ir = doc.document_ir
    links = nested_links(doc) if ir and ir.source.media_type == "application/pdf" else None
    parent_of = {child: owner for child, owner, _ in parent_cell_refs(doc, links) if owner}
    cell_table = {c.cell_id: t.table_id for t in doc.physical_tables for c in t.cells}
    contents = {c.content_id: c for c in doc.contents}
    containers = {}
    for child, owner in parent_of.items():
        record = containers.setdefault(owner, dict(items=[], status="linked", semantic_status="not_validated",
                                                   reading_order="unresolved", covered=False))
        record["items"].append(dict(kind="table", table=child))
    for link in links.cells if links else ():
        if link.containment_status == "unresolved":
            # The source cell itself can be known even when the child relationship is uncertain.
            exact = {s.reconstructed_cell_id for t in doc.physical_tables if t.page_number == link.page_number
                     for s in t.source_links if s.relation == "exact" and s.source_cell_id == link.parent_cell_id
                     and s.reconstructed_cell_id in cell_table}
            if len(exact) == 1:
                containers.setdefault(exact.pop(), dict(items=[], status=link.status, semantic_status=link.semantic_status,
                                                        reading_order=link.reading_order_status, covered=False))
            continue
        owners = {parent_of.get(child) for child in link.child_table_ids}
        if len(owners) != 1 or None in owners:
            continue
        owner = next(iter(owners))
        if {it.get("table") for it in containers[owner]["items"]} != set(link.child_table_ids):
            continue
        items = []
        for item in link.items:
            if item.kind == "table":
                items.append(dict(kind="table", table=item.ref_id))
            elif item.kind == "content":
                text = " ".join(((contents.get(item.ref_id) and contents[item.ref_id].text_raw) or "").split())
                if text:
                    items.append(dict(kind="text", text=text, content_id=item.ref_id))
            else:
                items.append(dict(kind=item.kind, ref=item.ref_id))
        # 칸 안 글자가 모두 안쪽 표·문장 항목으로 덮이고 순서가 정해졌을 때만, 칸 원문을 중복으로 본다.
        covered = (link.containment_status == "linked" and link.coverage_status == "complete"
                   and link.reading_order_status == "total"
                   and sum(len(it["text"]) for it in items if it["kind"] == "text") <= MAX_CONTEXT_CHARS)
        containers[owner] = dict(items=items, status=link.status, semantic_status=link.semantic_status,
                                        reading_order=link.reading_order_status, covered=covered)
    return dict(containers=containers, parent_of=parent_of, cell_table=cell_table,
                duplicates={cid for cid, v in containers.items() if v["covered"]})


def container_label(doc, cell_id):
    """담는 칸의 라벨 후보: 같은 표에서 그 칸의 행 범위에 걸치고 왼쪽에 있는 칸들의 원문(해석 없음)."""
    t, cell = next((t, c) for t in doc.physical_tables for c in t.cells if c.cell_id == cell_id)
    if cell.row_index is None or cell.column_index is None:
        return dict(table=t.table_id, r=cell.row_index, c=cell.column_index, cell_id=cell.cell_id,
                    label=[], label_cell_ids=[], labels=[], status="unresolved_coordinates")
    rows = set(range(cell.row_index, cell.row_index + (cell.rowspan or 1)))
    left = [c for c in t.cells if c.row_index is not None and c.column_index is not None
            and c.column_index < cell.column_index
            and rows & set(range(c.row_index, c.row_index + (c.rowspan or 1))) and (c.original_text or "").strip()]
    left = sorted(left, key=lambda c: c.column_index)
    # labels: 원문 그대로의 라벨 칸(인용·출처용). label: 공백만 정리한 표시용 글자.
    return dict(table=t.table_id, r=cell.row_index, c=cell.column_index, cell_id=cell.cell_id,
                label=[" ".join(c.original_text.split()) for c in left], label_cell_ids=[c.cell_id for c in left],
                labels=[dict(cell_id=c.cell_id, text_raw=c.original_text, span_refs=list(c.span_refs),
                             source_references=[c.source.model_dump(mode="json")]) for c in left])


def parents(doc, table_id, info):
    """안쪽 표가 담긴 위치: 바깥 → 안쪽 담는 칸 목록."""
    chain, seen = [], set()
    while table_id in info["parent_of"] and table_id not in seen:
        seen.add(table_id)
        holder = info["parent_of"][table_id]
        chain.append(dict(container_label(doc, holder), child_table=table_id))
        table_id = info["cell_table"][holder]
    return chain[::-1]


def container_context(doc, table_id, info=None):
    """안쪽 표의 담긴 위치 문맥: 담은 칸들의 라벨 경로(바깥 → 안쪽)와 칸마다 그 표(또는 다음 단계 표) 앞뒤의 문장.

    앞뒤 문장은 칸 안에서 이전 표와 다음 표 사이에 있는 원문이다(해석 없음). 무엇에 적용되는지는 판단하지 않는다.
    """
    info = info or containment(doc)
    levels = []
    for level in parents(doc, table_id, info):
        items = info["containers"][level["cell_id"]]["items"]
        at = next((i for i, it in enumerate(items) if it.get("table") == level["child_table"]), None)
        around = []
        if at is not None:
            start = max([i for i, it in enumerate(items[:at]) if it["kind"] != "text"], default=-1) + 1
            end = next((i for i in range(at + 1, len(items)) if items[i]["kind"] != "text"), len(items))
            around = [dict(content_id=it["content_id"], text_raw=it["text"], before=i < at)
                      for i, it in enumerate(items[start:end], start) if it["kind"] == "text"]
        levels.append(dict(level, around=around))
    return levels
