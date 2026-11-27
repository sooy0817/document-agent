# 역할: 원본 셀과 후보 행열 경로·단위·주석을 복원하고 값 조회 가능 여부를 표시한다.
import re
from doc_understanding.domain.numeric import parse_value
from doc_understanding.rag.evidence.containers import containment
from doc_understanding.rag.evidence.scope import layout_reviewed, table_scope


def display_path(path):
    return ' > '.join(x if x is not None else '[unavailable label]' for x in path)



def value_groups(st, cells, nodes):
    """같은 행에서 같은 행·열 경로를 가진 나란한 값 칸들(예: '해당' | 설명)을 한 값 묶음으로 본다.

    분석기는 같은 경로의 칸들을 충돌로 보류한다. 칸들이 같은 행 범위에 있고, 보류 이유가 충돌뿐이며
    (경로 노드·물리 셀은 확정), 경로가 온전할 때만 묶음으로 인정한다. 반환: 셀 ID → 묶음의 셀 ID 목록(열 순서).
    """
    blocked = {t for a in st.ambiguities if a.code == "CELL_PATH_UNRESOLVED" for t in a.target_ids}
    collided = {t for a in st.ambiguities if a.code == "PATH_COLLISION" for t in a.target_ids}
    by_path = {}
    for m in st.cell_mappings:
        if m.row_path and m.column_path:
            by_path.setdefault((m.row_path, m.column_path), []).append(m)
    groups = {}
    for (row_path, column_path), members in by_path.items():
        if len(members) < 2:
            continue
        physical = [cells[m.cell_id] for m in members]
        same_rows = len({(c.row_index, c.rowspan) for c in physical}) == 1
        clean = all(m.cell_id in collided and m.cell_id not in blocked for m in members)
        solid = (all(c.status != "unresolved" for c in physical)
                 and all(nodes[n].evidence_state != "unresolved" for n in row_path + column_path))
        if same_rows and clean and solid:
            ids = [c.cell_id for c in sorted(physical, key=lambda c: c.column_index)]
            groups.update({i: ids for i in ids})
    return groups


def header_from_corner_only(pt, st):
    """머리글 행 수가 '왼쪽 위 칸의 세로 병합 높이'로만 정해졌고, 그 구간이 머리글이라는 다른 근거가 없는가.

    분석기는 왼쪽 맨 위 칸이 여러 행에 걸쳐 있으면 그 높이를 머리글 행 수로 본다. 머리글 없이 앞 쪽에서 이어진 표에서는
    그 칸이 병합된 행 라벨이고(예: 단지 이름이 평형 세 줄에 걸친 칸), 위쪽 데이터 행들이 열 머리글로 붙는다. 실제 여러 줄
    머리글에는 아래 머리글들을 가로로 묶는 칸이 있다. 그런 칸이 머리글 구간 안에 하나도 없으면 두 해석을 가를 수 없다.
    칸의 글자는 보지 않고 점유 범위만 본다. 어느 해석이 맞는지는 정하지 않고 보류만 한다.
    """
    from doc_understanding.parser.structure.trees import ranges
    if len(st.panels) != 1 or st.panels[0].header_rows < 2:
        return False
    header = st.panels[0].header_rows
    spans = [s for s in (ranges(c) for c in pt.cells) if s]
    corners = [s for s in spans if s[0][0] == 0 and s[1][0] == 0]
    if len(corners) != 1 or corners[0][0][1] != header:
        return False
    return not any(rows[1] <= header and columns[1] - columns[0] > 1 for rows, columns in spans if (rows, columns) != corners[0])


_REVERSED = {}


def reversed_marker_links(doc):
    """문서의 번호 체계 순서와 반대로 상위 항목이 붙은 행 라벨 노드 ID.

    다른 행의 라벨을 상위로 붙일 때 분석은 번호 체계(로마 숫자, 숫자, 괄호 숫자, 가나다)의 상하를 '그 표에서 먼저 나온
    순서'로 정한다. 앞 쪽에서 이어진 표는 하위 번호가 먼저 나오므로, 뒤에 나온 상위 번호가 그 아래로 들어간다(예: '4.'
    아래의 'VIII.'). 문서 전체에서 (X 아래 Y)와 (Y 아래 X)가 함께 있으면 적은 쪽을 반대 방향으로 본다. 수가 같으면
    어느 쪽도 고르지 않는다. 번호의 뜻이나 고정된 깊이는 쓰지 않는다.
    """
    from collections import Counter
    from doc_understanding.domain.text_markers import number_marker
    kept = _REVERSED.get(id(doc))
    if kept is not None and kept[0] is doc:
        return kept[1]
    links = []
    for st in doc.table_structure:
        nodes = {n.node_id: n for n in st.row_tree}
        for n in st.row_tree:
            p = nodes.get(n.parent_id)
            if p is None or (p.row_range[0] <= n.row_range[0] and n.row_range[1] <= p.row_range[1]):
                continue  # 같은 행을 감싼 병합 라벨은 격자가 근거다
            a, b = number_marker(p.label_raw or ''), number_marker(n.label_raw or '')
            if a and b and a.family != b.family:
                links.append((a.family, b.family, n.node_id))
    pairs = Counter((a, b) for a, b, _ in links)
    found = frozenset(i for a, b, i in links if pairs[b, a] > pairs[a, b])
    if len(_REVERSED) >= 8:
        _REVERSED.clear()
    _REVERSED[id(doc)] = (doc, found)
    return found


def cells_for_table(doc, table_id):
    from doc_understanding.parser.relations.header_inheritance import effective_structure
    st = effective_structure(doc, table_id)
    pt = next(t for t in doc.physical_tables if t.table_id == table_id)
    borrowed = next((link for link in doc.logical_table_links
                     if link.target_table_id == table_id and link.status == 'accepted'
                     and link.algorithm == 'header-inheritance/1.0'), None)
    path_sources = {p.cell_id:p for p in borrowed.paths} if borrowed else {}
    originals = {c.cell_id:c for t in doc.physical_tables for c in t.cells} if borrowed else {}
    nodes = {n.node_id: n for n in st.row_tree + st.column_tree}
    cells = {c.cell_id: c for c in pt.cells}
    tc = next(c for c in doc.contents if c.table_id == table_id)
    contents = {c.content_id: c for c in doc.contents}
    context = [contents[cid] for cid in tc.context_content_ids]
    units = [dict(content_id=c.content_id, text_raw=c.text_raw, status='candidate')
             for c in context if re.search(r'단\s*위\s*[:：]|\bunit\s*:', c.text_raw or '', re.I)]
    scope = table_scope(st)
    reviewed = layout_reviewed(st)
    # 내용을 담는 칸의 원문은 안쪽 표·문장을 이어 붙인 것이다. 같은 글자가 안쪽 표 셀·칸 안 문장으로 따로 있으므로
    # 값 근거로 쓰지 않는다(중복 제외). 칸 안 연결이 불완전한 칸은 원문을 남긴다(rag.evidence.containers).
    duplicates = containment(doc)["duplicates"]
    from doc_understanding.rag.indexing.nested import nested_projection
    nested_context = nested_projection(doc)[0].get(table_id)
    groups = value_groups(st, cells, nodes)
    # 구조 해석이 확정으로 내놓았지만 근거가 한쪽뿐인 경우: 값 조회 근거에서 보류한다(저장된 구조는 바꾸지 않는다).
    weak_header = (doc.document_ir.source.media_type == 'application/pdf' and not reviewed
                   and header_from_corner_only(pt, st))
    reversed_links = reversed_marker_links(doc)
    result = []
    from doc_understanding.rag.evidence.document import document_label
    label = document_label(doc)
    for m in st.cell_mappings:
        cell = cells[m.cell_id]
        kind, number = parse_value(cell.original_text)
        notes = []
        for link in doc.context_links:
            if link.relation != 'note':
                continue
            applies = link.target_id == table_id or link.target_id in m.row_path or link.target_id == cell.cell_id
            if applies or (link.scope == 'unknown' and link.source.page_number == pt.page_number):
                note = next((c for c in context if link.source in c.source_references), None)
                if note:
                    notes.append(dict(text_raw=note.text_raw, target_id=link.target_id, scope=link.scope,
                                      status='candidate' if applies else 'unknown_scope'))
        row_labels = [nodes[n].label_raw for n in m.row_path]
        column_labels = [nodes[n].label_raw for n in m.column_path]
        container_duplicate = cell.cell_id in duplicates
        # 경로는 한쪽만 있어도 된다(머리글 없는 표의 행 경로, 라벨 없는 행의 열 경로). 있어야 할 축이 빈 매핑은
        # 분석기가 미확정으로 남기므로 여기서 걸러진다. 같은 경로의 나란한 칸 묶음은 충돌 보류를 푼다.
        resolved = m.evidence_state != 'unresolved' or m.cell_id in groups
        wrong_order = bool(reversed_links.intersection(m.row_path))
        eligible = bool((row_labels or column_labels) and all(row_labels + column_labels)
                        and resolved and cell.status != 'unresolved'
                        and not container_duplicate and not weak_header and not wrong_order)
        header_rows = max((p.header_rows for p in st.panels), default=1)
        table_context = [c.original_text for c in pt.cells if c.row_index is not None
                         and c.row_index < header_rows and c.original_text]
        result.append(dict(document_id=doc.document_id, source_name=doc.document_ir.source_name, document_label=label,
                           source_uri=doc.source_uri,source_media_type=doc.document_ir.source.media_type,
                           page=pt.page_number, table_id=table_id, cell_id=cell.cell_id,
                           value_raw=cell.original_text, value_kind=kind, numeric_candidate=number,
                           row_node_ids=list(m.row_path), column_node_ids=list(m.column_path),
                           row_path=row_labels, column_path=column_labels,
                           lookup_eligible=eligible, container_duplicate=container_duplicate,
                           value_group=groups.get(m.cell_id),
                           # 묶음의 다른 칸 원문을 함께 싣는다. 근거 한도에서 묶음이 갈라져도 나란한 칸 내용이 빠지지 않는다.
                           group_values=[dict(cell_id=i, text_raw=cells[i].original_text or '',
                                              span_refs=list(cells[i].span_refs),
                                              source_references=[cells[i].source.model_dump(mode='json')])
                                         for i in groups.get(m.cell_id, []) if i != m.cell_id],
                           table_context=table_context,
                           mapping_state=m.evidence_state, physical_state=cell.status,
                           semantic_status='unvalidated_candidate', unit_context=units, notes=notes,
                           scope_context=scope,
                           structure_review='layout_candidate' if reviewed else None,
                           source=cell.source.model_dump(mode='json'),
                           source_cell_ids=list(cell.source_cell_ids),
                           span_refs=list(cell.span_refs),
                           context_content_ids=list(tc.context_content_ids)))
        if cell.cell_id in path_sources:
            inherited = path_sources[cell.cell_id]
            result[-1]['path_sources'] = dict(link_id=borrowed.link_id, status=borrowed.status,
                path_state=inherited.state, reasons=list(inherited.reasons),
                columns=[dict(cell_id=cid, text_raw=originals[cid].original_text,
                              source_references=[originals[cid].source.model_dump(mode='json')])
                         for cid in inherited.column_source_cell_ids])
        if weak_header:
            result[-1]['path_withheld'] = 'header_from_corner_only'
        elif wrong_order:
            result[-1]['path_withheld'] = 'marker_order_reversed'
        if nested_context:
            # 칸 안에 담긴 표의 셀: 담은 칸 라벨 경로·칸 안 문장(적용 범위 미검증)을 근거와 함께 넘긴다.
            result[-1]['nested_context'] = nested_context
    withhold_label_collisions(result, cells)
    return result


def label_key(path):
    return tuple(re.sub(r'\s+', '', x or '') for x in path)


def withhold_label_collisions(evidence, cells):
    """행·열 라벨 글자 경로가 칸 하나를 가리키지 못하면 값 조회 근거로 쓰지 않는다.

    서로 다른 행의 두 값 칸이 같은 (행 라벨 글자, 열 라벨 글자) 경로를 가지면, 그 경로로는 어느 칸인지 정할 수 없다.
    행을 가르는 라벨이 경로에서 빠졌다는 뜻이다(예: 나이 | 가구원 수 | 값에서 나이만 행 라벨로 본 경우 — 같은 나이가
    여러 행에 있다). 분석기의 충돌 검사는 라벨 노드 ID로 비교해 이 경우를 못 본다. 어느 해석이 맞는지는 여기서 정하지
    않고 보류만 한다. 같은 행에 나란한 칸(값 묶음)은 대상이 아니다.
    """
    by_path = {}
    for e in evidence:
        if e['lookup_eligible']:
            by_path.setdefault((label_key(e['row_path']), label_key(e['column_path'])), []).append(e)
    for members in by_path.values():
        if len({(cells[e['cell_id']].row_index, cells[e['cell_id']].rowspan) for e in members}) > 1:
            for e in members:
                e.update(lookup_eligible=False, path_collision='label_text')
