# 역할: 원본 셀·좌표·역할·경로의 일관성을 검사하고 선택적 모델 제안의 ID 범위를 검증한다.
# 검증 통과는 의미 정답 승인이나 원본 IR 수정 권한을 뜻하지 않는다.
from doc_understanding.domain.models import Issue
from doc_understanding.parser.structure.models import VisionStructureRequest
from doc_understanding.parser.structure.repeated_panels import repeated_panel_ranges
from doc_understanding.parser.structure.trees import contains, ranges, physical_state, parent_candidates, path_for


# 원본 보존·역할/매핑 누락·부모 포함 관계·순환·패널 교차·경로 충돌을 검사해 Issue 목록을 반환한다.
def validate_hierarchy(physical, structured):
    issues = []
    cells = {c.cell_id: c for c in physical.cells}

    # 검사 실패를 물리 원본 출처와 함께 기록한다. 보완 가능한 누락은 경고로 구분할 수 있다.
    def issue(code, message, error=True):
        issues.append(
            Issue(
                code=code,
                severity="error" if error else "warning",
                message=message,
                source_references=physical.source_references,
            )
        )

    if physical.cells and not structured.cell_mappings:
        issue("NO_BODY_MAPPING", "Source cells exist but no body mapping was produced; role coverage is not semantic completeness.", False)
    if physical.rows and any(p.header_rows >= len(physical.rows) for p in structured.panels):
        issue("HEADER_CONSUMES_ALL_ROWS", "Proposed header covers every row; no-body interpretation requires review.", False)
    panel_index = {p.panel_id:p for p in structured.panels}
    if len(panel_index) != len(structured.panels):
        issue("DUPLICATE_PANEL_ID", "Panel IDs must be unique.")
    for m in structured.cell_mappings:
        c = cells.get(m.cell_id); p = panel_index.get(m.panel_id)
        if not p or (c and ranges(c) and not contains(p.column_range, ranges(c)[1])):
            issue("PANEL_GEOMETRY_CONFLICT", m.cell_id)
    suggested = repeated_panel_ranges(physical)
    if suggested:
        role_map = {r.cell_id:r.role for r in structured.cell_roles}
        misplaced = [c.cell_id for c in physical.cells if c.row_index > 0
                     and c.column_index in {a for a,b in suggested}
                     and (c.original_text or '').strip() and role_map.get(c.cell_id) == 'value']
        if misplaced:
            issue("REPEATED_STUB_AS_VALUE", "Repeated-panel label candidates classified as values: " + ", ".join(misplaced), False)

    nodes = structured.row_tree + structured.column_tree
    if len(cells) != len(physical.cells):
        issue("DUPLICATE_SOURCE_CELL_ID", "Physical cell IDs are not unique.")
    for c in physical.cells:
        span = ranges(c)
        if span:
            rr, cc = span
            rectangle = {(r, k) for r in range(*rr) for k in range(*cc)}
            if (
                set(c.occupied_slots) != rectangle
                or rr[0] < 0
                or cc[0] < 0
                or rr[1] > len(physical.rows)
                or cc[1] > len(physical.columns)
            ):
                issue("INVALID_SOURCE_OCCUPANCY", c.cell_id)
    index = {n.node_id: n for n in nodes}
    if len(index) != len(nodes):
        issue("DUPLICATE_NODE_ID", "Node IDs must be unique.")
    for n in nodes:
        if n.source_cell_id not in cells:
            issue("SOURCE_REF_MISSING", n.node_id)
            continue
        c = cells[n.source_cell_id]
        if n.label_raw != c.original_text:
            issue("SOURCE_TEXT_CHANGED", n.node_id)
        if ranges(c) != (n.row_range, n.column_range) or c.source_bbox != n.bbox:
            issue("SOURCE_GEOMETRY_MISMATCH", n.node_id)
        expected = physical_state(c, physical)
        if n.physical_evidence != expected or n.evidence_state == "observed":
            issue("EVIDENCE_PROMOTION", n.node_id)
        if expected == "unresolved" and n.evidence_state != "unresolved":
            issue("UNCERTAINTY_LOST", n.node_id)
        if any(i not in index for i in n.candidate_parent_ids):
            issue("CANDIDATE_PARENT_MISSING", n.node_id)
        if n.parent_id:
            p = index.get(n.parent_id)
            if (
                not p
                or p.axis != n.axis
                or p.panel_id != n.panel_id
                or p not in parent_candidates(n, [p])
            ):
                issue("PARENT_OCCUPANCY_CONFLICT", n.node_id)
            elif p.evidence_state == "unresolved" and n.evidence_state != "unresolved":
                issue("UNCERTAINTY_LOST", n.node_id)
        if not path_for(n, nodes):
            issue("TREE_CYCLE", n.node_id)
    roles = [r.cell_id for r in structured.cell_roles]
    if set(roles) != set(cells) or len(roles) != len(cells):
        issue("CELL_ROLE_COVERAGE", "Every physical cell needs exactly one role.")
    mapped = [m.cell_id for m in structured.cell_mappings]
    expected_values = {r.cell_id for r in structured.cell_roles if r.role == "value"}
    if set(mapped) != expected_values or len(mapped) != len(set(mapped)):
        issue("MAPPING_COVERAGE", "Every value cell needs exactly one mapping record.")
    seen = set()
    for m in structured.cell_mappings:
        c = cells.get(m.cell_id)
        if c is None or c.source_bbox != m.bbox or c.original_text != m.original_text:
            issue("MAPPING_SOURCE_MISMATCH", m.cell_id)
        for axis, path in (("row", m.row_path), ("column", m.column_path)):
            if path and (
                any(i not in index for i in path) or path != path_for(index[path[-1]], nodes)
            ):
                issue("INVALID_PATH", m.cell_id)
                continue
            if path and any(index[i].axis != axis or index[i].panel_id != m.panel_id for i in path):
                issue("PANEL_CROSS_MAPPING", m.cell_id)
            if path and c and ranges(c):
                n = index[path[-1]]
                if not contains(
                    n.row_range if axis == "row" else n.column_range, ranges(c)[axis == "column"]
                ):
                    issue("PATH_OCCUPANCY_CONFLICT", m.cell_id)
            if (
                path
                and any(index[i].evidence_state == "unresolved" for i in path)
                and m.evidence_state != "unresolved"
            ):
                issue("UNCERTAINTY_LOST", m.cell_id)
        if m.evidence_state == "observed" or (
            c and physical_state(c, physical) == "unresolved" and m.evidence_state != "unresolved"
        ):
            issue("EVIDENCE_PROMOTION", m.cell_id)
        if not m.row_path or not m.column_path:
            issue("CELL_UNMAPPED", m.cell_id, False)
        pair = m.row_path, m.column_path
        if pair in seen and all(pair):
            issue("PATH_COLLISION", m.cell_id, m.evidence_state != "unresolved")
        seen.add(pair)
    if (
        structured.physical_boundaries != physical.boundaries
        or structured.physical_unresolved_boundaries != physical.unresolved_boundaries
    ):
        issue("BOUNDARY_EVIDENCE_CHANGED", "Physical boundaries must remain unchanged.")
    return tuple(issues)


# 현재 물리 셀·구조 노드 ID만 허용하는 검토 요청을 만든다. 이 함수는 모델을 호출하지 않는다.
def vision_request(physical, structured):
    return VisionStructureRequest(
        physical=physical,
        structured=structured,
        ambiguities=structured.ambiguities,
        allowed_cell_ids=tuple(c.cell_id for c in physical.cells),
        allowed_node_ids=tuple(n.node_id for n in structured.row_tree + structured.column_tree),
    )


# 제안의 대상·부모·근거 ID와 자기 부모 참조를 검사한다. 통과해도 적용이나 정답 승인은 하지 않는다.
def validate_proposal(request, proposal):
    """Membership gate only, never applies or approves a proposed patch."""
    allowed = set(request.allowed_node_ids) | set(request.allowed_cell_ids)
    errors = []
    if proposal.target_id not in allowed:
        errors.append("UNKNOWN_TARGET")
    if proposal.parent_id and proposal.parent_id not in request.allowed_node_ids:
        errors.append("UNKNOWN_PARENT")
    if not proposal.evidence_cell_ids or not set(proposal.evidence_cell_ids) <= set(
        request.allowed_cell_ids
    ):
        errors.append("UNKNOWN_OR_MISSING_EVIDENCE")
    if proposal.operation == "propose_parent" and proposal.parent_id == proposal.target_id:
        errors.append("SELF_PARENT")
    return tuple(errors)
