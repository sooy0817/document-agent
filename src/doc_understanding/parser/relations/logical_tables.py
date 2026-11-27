# 역할: 물리 표 조각 사이의 연결과 경로 후보를 원본 ID로 표현하며 물리 표를 합쳐 덮어쓰지 않는다.
"""Conservative source-ID links between physical table fragments.

No physical cells or raw semantic trees are rewritten. Links and paths remain
review candidates, not financial facts. Geometry alone is insufficient to join.
"""
import re
from collections import defaultdict
from typing import Literal

from doc_understanding.domain.models import EvidenceModel


# 표 조각 간 연결에서 값 셀에 대응하는 원본 행·열 라벨 셀 ID와 경쟁 후보를 담는다.
class LogicalCellPath(EvidenceModel):
    cell_id: str
    row_source_cell_ids: tuple[str, ...] = ()
    column_source_cell_ids: tuple[str, ...] = ()
    row_candidate_cell_ids: tuple[str, ...] = ()
    state: Literal['inferred', 'unresolved'] = 'unresolved'
    reasons: tuple[str, ...] = ()


# 두 물리 표의 연결 후보와 빌려오는 헤더·행 문맥·경로를 기록한다. 원본 표는 병합하지 않는다.
class LogicalTableLink(EvidenceModel):
    link_id: str
    source_table_id: str
    target_table_id: str
    kind: Literal['detached_header', 'page_continuation']
    header_source_cell_ids: tuple[str, ...]
    inherited_row_source_cell_ids: tuple[str, ...]
    paths: tuple[LogicalCellPath, ...]
    reason_codes: tuple[str, ...]
    status: Literal['candidate', 'accepted'] = 'candidate'
    candidate_source_table_ids: tuple[str, ...] = ()
    semantic_verified: Literal[False] = False
    algorithm: Literal['logical-table-links/1.0', 'header-inheritance/1.0'] = 'logical-table-links/1.0'


# 비교를 위해 공백을 제거한다. 저장된 원문 문자열은 변경하지 않는다.
def norm(text):
    return re.sub(r'\s+', '', text or '')


# 행·열 위치와 병합 span이 있고 unresolved가 아닌 셀만 경로 계산에 사용한다.
def usable(c):
    return (c.row_index is not None and c.column_index is not None and c.rowspan is not None
            and c.colspan is not None and c.status != 'unresolved')


# 짧고 문장 종결 징후가 없는 사용 가능한 셀을 라벨 후보로 제한한다.
def compact(c):
    text = norm(c.original_text)
    return usable(c) and 0 < len(text) <= 48 and not re.search(r'[。!?]|[.!?]\s*$', c.original_text or '')


# 첫 행의 짧은 비숫자 라벨과 행 높이 조건을 검사해 헤더 띠 후보를 반환한다.
def header_band(table):
    cells = [c for c in table.cells if c.row_index == 0]
    if len(cells) < 2 or any(not compact(c) or c.rowspan != 1 for c in cells):
        return ()
    # A row of dates, amounts or prose is not evidence of a detached header.
    if any(sum(ch.isdigit() for ch in norm(c.original_text)) > 0 for c in cells):
        return ()
    if sum(c.colspan for c in cells) != len(table.columns):
        return ()
    if len(table.rows) > 1 and table.rows[0].end - table.rows[0].start >= table.rows[1].end - table.rows[1].start:
        return ()
    return tuple(sorted(cells, key=lambda c: c.column_index))


# 두 표의 열 개수와 각 열 양 끝 좌표가 허용 오차 안에서 같은지 검사한다.
def aligned(a, b):
    return len(a.columns) == len(b.columns) and len(a.columns) >= 2 and all(
        abs(x.start-y.start) <= 1.5 and abs(x.end-y.end) <= 1.5
        for x, y in zip(a.columns, b.columns))


# 상자 a가 충분히 더 크고 b를 허용 오차 안에서 포함하는지 검사한다.
def contains(a, b):
    return (a.x0 <= b.x0+1.5 and a.x1 >= b.x1-1.5 and
            a.y0 <= b.y0+1.5 and a.y1 >= b.y1-1.5 and
            (a.x1-a.x0)*(a.y1-a.y0) > 1.05*(b.x1-b.x0)*(b.y1-b.y0))


# 두 표 사이 가로 범위에 원문 텍스트 줄이 있는지 확인해 분리 근거를 찾는다.
def text_between(page, low, high, left, right):
    for block in page.blocks:
        lines = defaultdict(list)
        for span in block.spans:
            lines[span.line_index].append(span)
        for spans in lines.values():
            text = norm(''.join(s.text_raw for s in spans))
            y = sum((s.source.bbox.y0+s.source.bbox.y1)/2 for s in spans)/len(spans)
            if not text or ((y > page.height*.9 or y < page.height*.06)
                            and re.fullmatch(r'[-–—]?\d+[-–—]?', text)):
                continue
            if low+1.5 < y < high-1.5 and any(s.source.bbox.x1 > left and s.source.bbox.x0 < right for s in spans):
                return True
    return False


# 값 셀 왼쪽에서 해당 행 범위를 포함하는 짧은 라벨 셀을 열 순서대로 찾는다.
def row_labels(table, row_start, row_end, left_of):
    return sorted((c for c in table.cells if compact(c) and c.column_index+c.colspan <= left_of
                   and c.row_index <= row_start and c.row_index+c.rowspan >= row_end),
                  key=lambda c: c.column_index)


# 왼쪽부터 짧은 텍스트 라벨 열을 세고 숫자·본문 열에서 멈춘다.
def label_columns(table):
    """A contiguous short-text stub; stop at prose/numeric content columns."""
    width = 0
    for col in table.columns:
        values = [c for c in table.cells if usable(c) and c.column_index == col.index
                  and c.colspan == 1 and norm(c.original_text)]
        if any(not compact(c) or sum(ch.isdigit() for ch in norm(c.original_text)) /
               len(norm(c.original_text)) >= .5 for c in values):
            break
        width += 1
    return min(width, len(table.columns)-1)


# 대상 표 값 셀에 현재/상속 행 라벨과 헤더 원본 셀을 연결한다. 충돌과 문맥 누락은 unresolved로 남긴다.
def paths_for(source, target, heads, inherited):
    result = []
    stub = label_columns(target)
    for c in target.cells:
        if not usable(c) or not norm(c.original_text):
            continue
        labels = row_labels(target, c.row_index, c.row_index+c.rowspan, min(c.column_index, stub))
        # Never treat a previously encountered numeric value as an ancestor.
        labels = [v for v in labels if not re.fullmatch(r'[\d,.%+\-]+', norm(v.original_text))]
        blanks = [v for v in target.cells if usable(v) and not norm(v.original_text)
                  and v.row_index == 0
                  and v.row_index+v.rowspan >= c.row_index+c.rowspan
                  and v.column_index+v.colspan <= c.column_index]
        parents = [v for v in inherited if any(b.column_index <= v.column_index
                   and b.column_index+b.colspan >= v.column_index+v.colspan for b in blanks)]
        labels = sorted(parents + labels, key=lambda v:v.column_index)
        columns = [h for h in heads if h.column_index <= c.column_index
                   and h.column_index+h.colspan >= c.column_index+c.colspan]
        overlap = [v for v in target.cells if compact(v) and v.column_index+v.colspan <= min(c.column_index, stub)
                   and v.row_index < c.row_index+c.rowspan and v.row_index+v.rowspan > c.row_index]
        conflicts = [v for v in overlap if v not in labels and not any(
            l.column_index == v.column_index and l.row_index <= v.row_index
            and l.row_index+l.rowspan >= v.row_index+v.rowspan for l in labels)]
        reasons = ['SOURCE_GEOMETRY_CANDIDATE']
        if conflicts:
            reasons.append('MULTIPLE_ROW_SCOPES')
        if not columns:
            reasons.append('COLUMN_CONTEXT_UNKNOWN')
        if not labels:
            reasons.append('ROW_CONTEXT_UNKNOWN')
        result.append(LogicalCellPath(cell_id=c.cell_id,
            row_source_cell_ids=tuple(v.cell_id for v in labels),
            column_source_cell_ids=tuple(v.cell_id for v in columns),
            row_candidate_cell_ids=tuple(v.cell_id for v in conflicts),
            state='inferred' if labels and columns and not conflicts else 'unresolved',
            reasons=tuple(reasons)))
    return tuple(result)


# 같은 페이지 또는 인접 페이지 표 조각에서 헤더·열 정렬·분리 반례를 검사해 유일한 연결 후보만 만든다.
def build_logical_links(ir, physical):
    pages = {p.page_number: p for p in ir.pages}
    top = [t for t in physical if not any(o.table_id != t.table_id and o.page_number == t.page_number
           and contains(o.bounding_box, t.bounding_box) for o in physical)]
    top.sort(key=lambda t: (t.page_number, t.bounding_box.y0, t.bounding_box.x0))
    cells = {c.cell_id: c for t in physical for c in t.cells}
    links = []
    for target in top:
        proposals = []
        for source in top:
            if not aligned(source, target) or source.table_id == target.table_id:
                continue
            sb, tb = source.bounding_box, target.bounding_box
            heads = header_band(source)
            inherited = ()
            if source.page_number == target.page_number:
                gap = tb.y0-sb.y1
                if (len(source.rows) != 1 or not heads or not 0 <= gap <= min(20, (sb.y1-sb.y0)/2)
                        or text_between(pages[target.page_number], sb.y1, tb.y0, tb.x0, tb.x1)):
                    continue
                # Content must contrast with the short heading band.
                if not any(len(norm(c.original_text)) > 48 or re.search(r'\d', c.original_text or '')
                           for c in target.cells):
                    continue
                kind = 'detached_header'
                reasons = ('ALIGNED_COLUMN_BOUNDARIES', 'SHORT_HEADER_BAND', 'NO_INTERVENING_TEXT', 'BODY_CONTRAST')
            elif target.page_number == source.page_number+1:
                before, after = pages[source.page_number], pages[target.page_number]
                if sb.y1 < before.height*.85 or tb.y0 > after.height*.15:
                    continue
                if (text_between(before, sb.y1, before.height, sb.x0, sb.x1)
                        or text_between(after, 0, tb.y0, tb.x0, tb.x1)):
                    continue
                start = next((c for c in target.cells if c.row_index == c.column_index == 0 and usable(c)), None)
                last = [c for c in source.cells if usable(c) and c.column_index == 0
                        and c.row_index+c.rowspan == len(source.rows) and compact(c)]
                if start is None or len(last) != 1:
                    continue
                # Blank continuation or repeated leading group is mandatory.
                if norm(start.original_text) and norm(start.original_text) != norm(last[0].original_text):
                    continue
                blanks = [c for c in target.cells if usable(c) and c.row_index == 0
                          and not norm(c.original_text) and c.column_index+c.colspan <= label_columns(target)]
                inherited = tuple(c for c in row_labels(source,len(source.rows)-1,len(source.rows),label_columns(target))
                                  if any(b.column_index <= c.column_index and
                                         b.column_index+b.colspan >= c.column_index+c.colspan for b in blanks))
                prior = next((l for l in links if l.target_table_id == source.table_id), None)
                if not heads and prior:
                    heads = tuple(cells[cid] for cid in prior.header_source_cell_ids)
                kind = 'page_continuation'
                reasons = ('ALIGNED_COLUMN_BOUNDARIES', 'PAGE_EDGE_ADJACENCY', 'NO_INTERVENING_TEXT',
                           'BLANK_LEADING_CELL' if not norm(start.original_text) else 'REPEATED_LEADING_GROUP')
            else:
                continue
            proposals.append(LogicalTableLink(link_id=source.table_id+'/to/'+target.table_id,
                source_table_id=source.table_id,target_table_id=target.table_id,kind=kind,
                header_source_cell_ids=tuple(c.cell_id for c in heads),
                inherited_row_source_cell_ids=tuple(c.cell_id for c in inherited),
                paths=paths_for(source,target,heads,inherited),reason_codes=reasons))
        # Competing origins are not resolved by nearest-neighbour guessing.
        if len(proposals) == 1:
            links.append(proposals[0])
    return tuple(links)


# 표·셀 참조와 연결 범위·경로 출처의 무결성을 검사하고 위반 시 예외를 발생시킨다.
def validate_logical_links(doc):
    tables = {t.table_id: t for t in doc.physical_tables}
    owner = {c.cell_id: t.table_id for t in doc.physical_tables for c in t.cells}
    seen = set()
    predecessors = {}
    for link in doc.logical_table_links:
        if link.target_table_id in seen or link.source_table_id not in tables or link.target_table_id not in tables:
            raise ValueError('LOGICAL_TABLE_MEMBERSHIP_OR_UNIQUENESS')
        seen.add(link.target_table_id)
        predecessors[link.target_table_id] = link.source_table_id
        a,b = tables[link.source_table_id],tables[link.target_table_id]
        if link.status=='accepted':
            from doc_understanding.parser.relations.header_inheritance import pair_reasons
            structures={t.table_id:t for t in doc.table_structure}
            st=structures[a.table_id]
            if (link.algorithm!='header-inheritance/1.0' or b.page_number!=a.page_number+1
                    or len(st.panels)!=1 or pair_reasons(a,b,{p.page_number:p for p in doc.document_ir.pages},st.panels[0].header_rows)
                    or link.candidate_source_table_ids):
                raise ValueError('ACCEPTED_HEADER_LINK_WITHOUT_EVIDENCE')
        if not ((a.page_number, a.bounding_box.y0) < (b.page_number,b.bounding_box.y0)):
            raise ValueError('LOGICAL_TABLE_ORDER')
        ancestors = {link.source_table_id}
        cursor = link.source_table_id
        while cursor in predecessors:
            cursor = predecessors[cursor]
            if cursor in ancestors:
                raise ValueError('LOGICAL_TABLE_CYCLE')
            ancestors.add(cursor)
        for cid in link.header_source_cell_ids+link.inherited_row_source_cell_ids:
            if cid not in owner or owner[cid] not in ancestors:
                raise ValueError('LOGICAL_SOURCE_CELL_MISSING')
        if link.status=='accepted':
            nodes={n.source_cell_id:n for t in doc.table_structure for n in t.column_tree}
            if not link.header_source_cell_ids or any(cid not in nodes or nodes[cid].evidence_state=='unresolved'
                                                     for cid in link.header_source_cell_ids):
                raise ValueError('ACCEPTED_HEADER_SOURCE_UNRESOLVED')
        if len({p.cell_id for p in link.paths}) != len(link.paths):
            raise ValueError('LOGICAL_DUPLICATE_PATH')
        for path in link.paths:
            if owner.get(path.cell_id) != link.target_table_id:
                raise ValueError('LOGICAL_TARGET_CELL_MISSING')
            refs = path.row_source_cell_ids+path.column_source_cell_ids+path.row_candidate_cell_ids
            if path.cell_id in refs or any(cid not in owner for cid in refs):
                raise ValueError('LOGICAL_PATH_SOURCE_MISSING_OR_SELF')
            allowed = set(link.header_source_cell_ids+link.inherited_row_source_cell_ids)
            if any(owner[cid] != link.target_table_id and cid not in allowed for cid in refs):
                raise ValueError('LOGICAL_PATH_OUTSIDE_LINK_SCOPE')
