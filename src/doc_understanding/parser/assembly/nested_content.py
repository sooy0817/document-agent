# 역할: 원본 span·좌표로 부모 셀 안의 본문과 자식 표를 연결해 별도 결과를 만든다. 의미적 종속 관계를 확정하지 않는다.
"""Conditional, geometric cell-content links. Original IRs are never mutated."""
import hashlib
import json
from collections import Counter
from typing import Literal

from doc_understanding.domain.models import BBox, EvidenceModel
from doc_understanding.parser.physical.table_identity import TableIdentity, match_tables
from doc_understanding.parser.physical.partial_representation import PartialRepresentation, partial_representations


# 부모 셀 안에 위치한 본문 또는 표의 ID·좌표·원문 span을 참조한다.
class ContentItem(EvidenceModel):
    kind: Literal['content', 'table', 'native_table']
    ref_id: str
    bbox: BBox
    span_ids: tuple[str, ...] = ()


# 자식 표의 가능한 부모와 선택 결과·보류 이유를 기록한다.
class ContainmentCandidate(EvidenceModel):
    child_ref_id: str
    native_table_id: str | None
    physical_table_ids: tuple[str, ...]
    page_number: int
    parent_candidate_ids: tuple[str, ...]
    selected_parent_id: str | None
    reasons: tuple[str, ...]


# 세로 순서·한 줄 조각·병렬 배치 등 읽기 순서 후보의 묶음이다.
class ReadingGroup(EvidenceModel):
    kind: Literal['single', 'line', 'parallel', 'ambiguous']
    item_ref_ids: tuple[str, ...]
    bbox: BBox


# 부모 셀의 자식 항목과 span 누락·중복·범위 초과, 포함·읽기 순서 상태를 따로 보관한다.
class CellContent(EvidenceModel):
    parent_cell_id: str
    parent_native_table_id: str
    page_number: int
    child_table_ids: tuple[str, ...]
    items: tuple[ContentItem, ...]
    ignored_whitespace_span_ids: tuple[str, ...]
    uncovered_span_ids: tuple[str, ...]
    duplicate_span_ids: tuple[str, ...]
    outside_parent_span_ids: tuple[str, ...]
    reasons: tuple[str, ...]
    status: Literal['geometrically_linked', 'unresolved']
    semantic_status: Literal['not_validated'] = 'not_validated'
    containment_status: Literal['linked', 'unresolved', 'not_assessed'] = 'not_assessed'
    coverage_status: Literal['complete', 'unresolved', 'not_assessed'] = 'not_assessed'
    reading_order_status: Literal['total', 'partial', 'unresolved', 'not_assessed'] = 'not_assessed'
    reading_groups: tuple[ReadingGroup, ...] = ()


# 페이지별 중첩 분석 경로와 후보 수를 기록한다.
class PageRoute(EvidenceModel):
    page_number: int
    route: Literal['skip_no_candidate', 'process_candidates']
    candidate_count: int


# 문서 스냅샷 해시에 묶인 중첩 연결 결과다. 원본 표 대응·부분 표현 감사 정보도 포함한다.
class NestedContent(EvidenceModel):
    schema_version: Literal['nested-cell-content/1.0'] = 'nested-cell-content/1.0'
    document_id: str
    canonical_sha256: str
    coordinate_tolerance_pt: float = 1.5
    pages: tuple[PageRoute, ...]
    candidates: tuple[ContainmentCandidate, ...]
    cells: tuple[CellContent, ...]
    facts_published: Literal[0] = 0
    linkage_algorithm: str = 'bbox/1'
    table_identities: tuple[TableIdentity, ...] = ()
    partial_representations: tuple[PartialRepresentation, ...] = ()


# 문서를 정렬된 JSON으로 직렬화해 연결 결과가 참조하는 스냅샷의 해시를 계산한다.
def snapshot_hash(doc):
    raw = json.dumps(doc.model_dump(mode='json'), sort_keys=True,
                     separators=(',', ':'), ensure_ascii=False).encode('utf8')
    return hashlib.sha256(raw).hexdigest()


# 너비·높이를 0 이상으로 제한해 좌표 상자의 면적을 구한다.
def area(b):
    return max(0, b.x1-b.x0) * max(0, b.y1-b.y0)


# 자식이 충분히 작고 부모 안에 포함될 때만 참으로 판정한다. 동일 크기 중복 표는 제외한다.
def contains(child, parent, tolerance=1.5):
    """Strict smaller-region containment; equal/duplicate tables are excluded."""
    return (area(child) > 0 and area(child) < .95 * area(parent)
            and child.x0 >= parent.x0-tolerance and child.y0 >= parent.y0-tolerance
            and child.x1 <= parent.x1+tolerance and child.y1 <= parent.y1+tolerance)


# 상자의 네 좌표가 모두 허용 오차 안에서 같은지 검사한다.
def same_box(a, b, tolerance=1.5):
    return max(abs(x-y) for x, y in zip(a.xyxy, b.xyxy)) <= tolerance


# 다른 모든 후보 안에 포함되는 유일한 가장 안쪽 부모 셀만 선택한다. 경쟁 후보가 있으면 None이다.
def choose_owner(parents):
    """Select an innermost cell only when every other candidate encloses it."""
    if not parents:
        return None
    winners = [p for p in parents if all(
        p.cell_id == other.cell_id or contains(p.source.bbox, other.source.bbox)
        for other in parents)]
    return winners[0].cell_id if len(winners) == 1 else None


# 항목을 y/x 좌표로 정렬하고 세로 범위가 겹쳐 읽기 순서가 불명확한 경우 사유를 남긴다.
def order_items(items):
    ordered = tuple(sorted(items, key=lambda x: (x.bbox.y0, x.bbox.x0, x.ref_id)))
    reasons = set()
    for i, a in enumerate(ordered):
        for b in ordered[i+1:]:
            overlap = min(a.bbox.y1, b.bbox.y1)-max(a.bbox.y0, b.bbox.y0)
            height = min(a.bbox.y1-a.bbox.y0, b.bbox.y1-b.bbox.y0)
            # Side-by-side or overlapping blocks need a layout decision, not y/x certainty.
            if overlap > max(1.5, .2*height):
                reasons.add('READING_ORDER_AMBIGUOUS')
    return ordered, reasons


# 여러 항목의 상자를 모두 포함하는 외접 상자를 만든다.
def _union(refs):
    return BBox(x0=min(r.bbox.x0 for r in refs), y0=min(r.bbox.y0 for r in refs),
                x1=max(r.bbox.x1 for r in refs), y1=max(r.bbox.y1 for r in refs))


# 세로 순서·원문 한 줄 조각·병렬 열을 구분해 항목과 그룹·상태·사유를 반환한다.
def group_reading_order(items, spans):
    """Separate vertical sequence, inline LTR fragments and unordered columns.

    The flat list is serialization only inside a parallel/ambiguous group.
    Inline grouping needs original single-line span boxes and horizontal direction;
    no text labels, table dimensions or document-specific language patterns.
    """
    pending=list(sorted(items,key=lambda x:(x.bbox.y0,x.bbox.x0,x.ref_id)))
    groups=[];ordered=[];reasons=set();status='total'
    # 항목의 세로 겹침이 허용 길이를 넘는지 판정해 같은 읽기 그룹 후보를 찾는다.
    def overlaps(a,b):
        height=min(a.bbox.y1-a.bbox.y0,b.bbox.y1-b.bbox.y0)
        return min(a.bbox.y1,b.bbox.y1)-max(a.bbox.y0,b.bbox.y0)>max(1.5,.2*height)
    while pending:
        members=[pending.pop(0)]
        while True:
            attached=[x for x in pending if any(overlaps(x,m) for m in members)]
            if not attached:break
            members.extend(attached);pending=[x for x in pending if x not in attached]
        members.sort(key=lambda x:(x.bbox.x0,x.bbox.y0,x.ref_id))
        separated=all(a.bbox.x1<=b.bbox.x0 for a,b in zip(members,members[1:]))
        kind='single'
        if len(members)>1:
            line=separated and all(m.kind=='content' for m in members)
            baseline=members[0].bbox
            line=line and all(abs(m.bbox.y0-baseline.y0)<=1.5 and abs(m.bbox.y1-baseline.y1)<=1.5 for m in members)
            line=line and all(b.bbox.x0-a.bbox.x1<=2*min(a.bbox.y1-a.bbox.y0,b.bbox.y1-b.bbox.y0)
                              for a,b in zip(members,members[1:]))
            for m in members:
                if not m.span_ids:line=False
                for sid in m.span_ids:
                    s=spans.get(sid)
                    if s is None or tuple(getattr(s,'direction',()))!=(1,0):line=False;continue
                    b=s.source.bbox
                    if abs(b.y0-m.bbox.y0)>1.5 or abs(b.y1-m.bbox.y1)>1.5:line=False
            if line:kind='line'
            elif separated and all(m.kind in ('table','native_table') for m in members):
                kind='parallel';reasons.add('READING_ORDER_PARTIAL')
                if status=='total':status='partial'
            else:
                kind='ambiguous';status='unresolved';reasons.add('READING_ORDER_AMBIGUOUS')
        groups.append(ReadingGroup(kind=kind,item_ref_ids=tuple(m.ref_id for m in members),bbox=_union(members)))
        ordered.extend(members)
    return tuple(ordered),tuple(groups),status,reasons


# 원본 표 대응과 포함 관계로 부모 셀을 찾고 span 피복·읽기 순서를 별도 후보 결과로 기록한다.
def build_nested_content(doc):
    if doc.document_ir is None:
        raise ValueError('ORIGINAL_DOCUMENT_IR_REQUIRED')
    candidates, cells, routes, identities, partials = [], [], [], [], []
    for page in doc.document_ir.pages:
        physical = [t for t in doc.physical_tables if t.page_number == page.page_number]
        native_cells = {c.cell_id: (t.table_candidate_id, c)
                        for t in page.tables for c in t.cells}
        spans = {s.span_id: s for b in page.blocks for s in b.spans}
        match_ids, identity_audit = match_tables(page, physical)
        identities.extend(identity_audit)
        ambiguous_native = {a.native_table_id for a in identity_audit if a.status == 'ambiguous'}
        ambiguous_physical = {a.physical_table_id for a in identity_audit if a.status == 'ambiguous'}
        aliases, partial_audit = partial_representations(
            page, [p for p in physical if p.table_id not in ambiguous_physical], set(match_ids)|ambiguous_native)
        partials.extend(partial_audit)
        children, matched_physical = [], set()
        # A competing identity must not re-enter through the physical-only fallback.
        matched_physical.update(a.physical_table_id for a in identity_audit if a.status == 'ambiguous')
        for t in page.tables:
            if len(t.cells) < 2 or t.table_candidate_id in aliases:
                continue
            matches = [p for p in physical if p.table_id in match_ids.get(t.table_candidate_id, ())]
            matched_physical.update(p.table_id for p in matches)
            # Both source representations must fit the same parent. Do not widen
            # the parent tolerance to accept a child protruding beyond its cell.
            bbox = _union([t.source]+[t.source.model_copy(update={'bbox':p.bounding_box}) for p in matches])
            children.append((t.table_candidate_id, t.table_candidate_id, bbox,
                             tuple(matches), set(s for c in t.cells for s in c.span_refs)))
        for p in physical:
            if p.table_id not in matched_physical and len(p.cells) >= 2:
                children.append((p.table_id, None, p.bounding_box, (p,),
                                 set(s for c in p.cells for s in c.span_refs)))
        selected, count = {}, 0
        for child_id, native_id, bbox, matches, child_spans in children:
            parents = [c for tid, c in native_cells.values()
                       if tid != native_id and contains(bbox, c.source.bbox)]
            if not parents:
                continue
            count += 1
            owner = choose_owner(parents)
            reasons = {'STRICT_BBOX_CONTAINMENT'}
            if native_id in ambiguous_native:
                reasons.add('TABLE_IDENTITY_NOT_ONE_TO_ONE')
            if owner is None:
                reasons.add('COMPETING_PARENT_CELLS')
            if len(matches) != 1:
                reasons.add('PHYSICAL_CHILD_MISSING' if not matches else 'PHYSICAL_CHILD_AMBIGUOUS')
            if owner and not child_spans.issubset(set(native_cells[owner][1].span_refs)):
                reasons.add('CHILD_SPANS_OUTSIDE_PARENT')
            if not child_spans:
                reasons.add('NO_CHILD_SPAN_SUPPORT')
            candidates.append(ContainmentCandidate(
                child_ref_id=child_id, native_table_id=native_id,
                physical_table_ids=tuple(p.table_id for p in matches),
                page_number=page.page_number,
                parent_candidate_ids=tuple(sorted(p.cell_id for p in parents)),
                selected_parent_id=owner, reasons=tuple(sorted(reasons))))
            if owner:
                if len(matches) == 1:
                    p = matches[0]
                    # Physical text ownership is verified, never copied from parent cell text.
                    item = ContentItem(kind='table', ref_id=p.table_id, bbox=p.bounding_box,
                                       span_ids=tuple(sorted({s for c in p.cells for s in c.span_refs})))
                else:
                    item = ContentItem(kind='native_table', ref_id=child_id, bbox=bbox,
                                       span_ids=tuple(sorted(child_spans)))
                selected.setdefault(owner, []).append((item, reasons))
        routes.append(PageRoute(page_number=page.page_number,
                                route='process_candidates' if count else 'skip_no_candidate',
                                candidate_count=count))
        # Expensive content association is conditional on a selected nested owner.
        for owner, entries in sorted(selected.items()):
            parent_table, parent = native_cells[owner]
            parent_spans = set(parent.span_refs)
            items = [item for item, _ in entries]
            reasons = set().union(*(r for _, r in entries)) - {'STRICT_BBOX_CONTAINMENT'}
            containment_reasons = set(reasons)
            table_spans = {s for item in items for s in item.span_ids}
            whitespace = {s for s in parent_spans if s in spans and not spans[s].text_raw.strip()}
            for content in doc.contents:
                if content.kind == 'table' or not content.source_references:
                    continue
                refs = content.source_references
                if refs[0].page_number != page.page_number:
                    continue
                ids = {r.span_id for r in refs if r.span_id}
                if not ids.intersection(parent_spans - table_spans - whitespace):
                    continue
                if not ids.issubset(parent_spans) or ids.intersection(table_spans):
                    reasons.add('CONTENT_SPAN_OWNERSHIP_CONFLICT')
                    continue
                items.append(ContentItem(kind='content', ref_id=content.content_id,
                                         bbox=_union(refs), span_ids=tuple(sorted(ids))))
            ordered, groups, order_status, order_reasons = group_reading_order(items, spans)
            reasons.update(order_reasons)
            occurrences = Counter(s for item in ordered for s in item.span_ids)
            covered = set(occurrences)
            missing = parent_spans - covered - whitespace
            duplicates = {s for s, n in occurrences.items() if n > 1}
            outside = covered - parent_spans
            if missing:
                reasons.add('UNCOVERED_PARENT_SPANS')
            if duplicates:
                reasons.add('DUPLICATE_SPAN_OWNERSHIP')
            if outside:
                reasons.add('ITEM_SPANS_OUTSIDE_PARENT')
            cells.append(CellContent(
                parent_cell_id=owner, parent_native_table_id=parent_table,
                page_number=page.page_number,
                child_table_ids=tuple(item.ref_id for item, _ in entries if item.kind == 'table'),
                items=ordered, ignored_whitespace_span_ids=tuple(sorted(whitespace-covered)),
                uncovered_span_ids=tuple(sorted(missing)), duplicate_span_ids=tuple(sorted(duplicates)),
                outside_parent_span_ids=tuple(sorted(outside)), reasons=tuple(sorted(reasons)),
                containment_status='unresolved' if containment_reasons else 'linked',
                coverage_status='unresolved' if missing or duplicates or outside or 'CONTENT_SPAN_OWNERSHIP_CONFLICT' in reasons else 'complete',
                reading_order_status=order_status, reading_groups=groups,
                status='unresolved' if reasons else 'geometrically_linked'))
    return NestedContent(document_id=doc.document_id, canonical_sha256=snapshot_hash(doc),
                         pages=tuple(routes), candidates=tuple(candidates), cells=tuple(cells),
                         linkage_algorithm='span-geometry/2', table_identities=tuple(identities),
                         partial_representations=tuple(partials))


# 문서 해시·알고리즘·참조를 검사하고 연결을 재계산해 결과가 동일한지 검증한다. 의미 정답은 승인하지 않는다.
def validate_links(doc, links):
    if links.document_id != doc.document_id or links.canonical_sha256 != snapshot_hash(doc):
        raise ValueError('SNAPSHOT_MISMATCH')
    if links.linkage_algorithm != 'span-geometry/2':
        raise ValueError('LINKAGE_ALGORITHM_MISMATCH: preserve old sidecar; generate a new output')
    if links != build_nested_content(doc):
        raise ValueError('LINK_RECOMPUTATION_MISMATCH')
    return dict(references_valid=True, semantics_validated=False,
                candidate_count=len(links.candidates), linked_cells=len(links.cells),
                unresolved_cells=sum(c.status == 'unresolved' for c in links.cells))


# 검증된 참조로 부모 셀·본문·자식 표의 기존 JSON을 가져온다. 중첩 표를 평탄화하거나 재해석하지 않는다.
def resolve_cell_content(doc, links, parent_cell_id):
    """Hydrate existing JSON evidence; does not flatten or reinterpret nested tables."""
    validate_links(doc, links)
    cell = next(c for c in links.cells if c.parent_cell_id == parent_cell_id)
    parent = next(c for p in doc.document_ir.pages for t in p.tables for c in t.cells
                  if c.cell_id == parent_cell_id)
    native = {t.table_candidate_id: t for p in doc.document_ir.pages for t in p.tables}
    physical = {t.table_id: t for t in doc.physical_tables}
    structured = {t.table_id: t for t in doc.table_structure}
    contents = {c.content_id: c for c in doc.contents}
    items = []
    for item in cell.items:
        output = item.model_dump(mode='json')
        if item.kind == 'table':
            output['physical'] = physical[item.ref_id].model_dump(mode='json')
            output['structured'] = (structured[item.ref_id].model_dump(mode='json')
                                    if item.ref_id in structured else None)
        elif item.kind == 'native_table':
            output['native'] = native[item.ref_id].model_dump(mode='json')
        else:
            output['content'] = contents[item.ref_id].model_dump(mode='json')
        items.append(output)
    return dict(parent_cell=parent.model_dump(mode='json'),
                linkage=cell.model_dump(mode='json'), items=items)
