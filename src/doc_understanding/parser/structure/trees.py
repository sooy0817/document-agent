# 역할: 물리 점유 범위로 행열 부모 후보와 경로를 구성한다. 모호한 부모와 불확실성은 보존한다.
# 행과 열은 같은 포함 관계 계산을 공유하며 원본에 없는 라벨을 만들지 않는다.
from doc_understanding.parser.structure.models import HierarchyNode


# 셀의 점유 슬롯을 행·열의 반열린 구간 [시작, 끝)으로 바꾼다. 점유가 없으면 None이다.
def ranges(cell):
    if not cell.occupied_slots:
        return None
    rr, cc = zip(*cell.occupied_slots)
    return (min(rr), max(rr) + 1), (min(cc), max(cc) + 1)


# 셀 상태와 참조 경계 상태에서 물리 근거의 불확실성을 모은다. 의미 정답 여부는 판단하지 않는다.
def physical_state(cell, table):
    edges = {b.boundary_id: b for b in table.boundaries}
    states = [edges[i].status for i in cell.boundary_ids if i in edges]
    if cell.status == "unresolved" or "uncertain" in states:
        return "unresolved"
    if cell.status == "inferred" or "inferred" in states:
        return "inferred"
    return "observed"


# 반열린 구간 inner가 outer 안에 완전히 들어오는지 검사한다. 단순 겹침과 구분한다.
def contains(outer, inner):
    return outer[0] <= inner[0] and inner[1] <= outer[1]


# 행 축은 왼쪽, 열 축은 위쪽에서 자식 범위를 포함하는 가장 가까운 부모 후보를 찾는다.
# 호출자가 같은 축·패널의 노드 집합을 넘겨야 한다. 동률 후보를 임의로 하나로 줄이지 않는다.
def parent_candidates(node, nodes):
    if node.axis == "row":
        candidates = [
            p
            for p in nodes
            if p.column_range[1] <= node.column_range[0] and contains(p.row_range, node.row_range)
        ]
        nearest = max((p.column_range[1] for p in candidates), default=-1)
        return [p for p in candidates if p.column_range[1] == nearest]
    candidates = [
        p
        for p in nodes
        if p.row_range[1] <= node.row_range[0] and contains(p.column_range, node.column_range)
    ]
    nearest = max((p.row_range[1] for p in candidates), default=-1)
    return [p for p in candidates if p.row_range[1] == nearest]


# 라벨 셀을 원본 ID·문구·좌표가 연결된 노드로 만들고 유일한 부모만 parent_id로 연결한다.
# 부모가 여러 개면 candidate_parent_ids에 남기고, 조상의 unresolved 상태를 자손까지 전파한다.
def build_tree(cells, axis, panel, table):
    nodes = []
    for c in sorted(cells, key=lambda c: (ranges(c)[0], ranges(c)[1], c.cell_id)):
        rr, cc = ranges(c)
        state = physical_state(c, table)
        nodes.append(
            HierarchyNode(
                node_id=f"{panel.panel_id}/{axis}/{c.cell_id}",
                axis=axis,
                panel_id=panel.panel_id,
                label_raw=c.original_text,
                source_cell_id=c.cell_id,
                bbox=c.source_bbox,
                row_range=rr,
                column_range=cc,
                physical_evidence=state,
                evidence_state="unresolved"
                if state == "unresolved" or not (c.original_text or "").strip()
                else "inferred",
                boundary_ids=c.boundary_ids,
            )
        )
    connected = []
    for n in nodes:
        parents = parent_candidates(n, nodes)
        connected.append(
            n.model_copy(
                update={
                    "parent_id": parents[0].node_id if len(parents) == 1 else None,
                    "candidate_parent_ids": tuple(p.node_id for p in parents)
                    if len(parents) > 1
                    else (),
                    "evidence_state": "unresolved" if len(parents) > 1 else n.evidence_state,
                }
            )
        )
    # Propagate uncertainty through the complete ancestor chain.
    index = {n.node_id: n for n in connected}
    for _ in connected:
        changed = False
        for key, n in list(index.items()):
            if (
                n.parent_id
                and index[n.parent_id].evidence_state == "unresolved"
                and n.evidence_state != "unresolved"
            ):
                index[key] = n.model_copy(update={"evidence_state": "unresolved"})
                changed = True
        if not changed:
            break
    return tuple(index.values())


# parent_id를 따라 조상으로 올라간 뒤 뒤집어 루트 → 현재 노드의 ID 경로를 반환한다.
# 순환은 빈 경로로 반환한다. 누락된 부모에서는 탐색이 끝나므로 참조 무결성은 별도 검증이 필요하다.
def path_for(node, nodes):
    index = {n.node_id: n for n in nodes}
    path, seen = [], set()
    while node:
        if node.node_id in seen:
            return ()
        seen.add(node.node_id)
        path.append(node.node_id)
        node = index.get(node.parent_id)
    return tuple(reversed(path))


# 값 셀에 대응하는 축의 말단 후보를 고른다. 행은 범위를 포함하는 가장 깊은 노드를 사용한다.
# 열은 포함하는 leaf를 우선하고 없으면 겹치는 leaf를 후보로 남긴다. 유일성 판단은 analyzer가 한다.
def leaves_for(cell, nodes, axis):
    span = ranges(cell)[0 if axis == "row" else 1]
    if axis == "column":
        parents = {n.parent_id for n in nodes}
        leaves = [n for n in nodes if n.node_id not in parents]
        containing = [n for n in leaves if contains(n.column_range, span)]
        return containing or [
            n for n in leaves if max(n.column_range[0], span[0]) < min(n.column_range[1], span[1])
        ]
    candidates = [
        n for n in nodes if contains(n.row_range if axis == "row" else n.column_range, span)
    ]
    # Only the deepest matching nodes are eligible; intersecting, non-containing
    # merged ranges never make a child.
    ancestors = {i for n in candidates for i in path_for(n, nodes)[:-1]}
    return [n for n in candidates if n.node_id not in ancestors]
