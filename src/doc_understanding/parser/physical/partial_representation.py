# 역할: 같은 열 구간이 중복 표현되었는지 검사하며 어느 쪽 원본도 수정하지 않는다.
"""Audit a complete column strip represented twice; never edit either source IR.

Subset spans alone are insufficient. Every native cell must coincide with one
complete physical cell, with identical source IDs, across the full grid height.
This deliberately excludes inset/partial-height nested grids.
"""
from collections import Counter
from typing import Literal
from doc_understanding.domain.models import EvidenceModel
from doc_understanding.parser.physical.table_identity import supports, overlap


# 원본의 행 조각이 공란을 포함해 물리 셀 전체를 완전히 분할하는 경우에만 대응을 반환한다.
def complete_row_partition(native, physical, nonblank):
    """A full-width row strip must tile complete cells, including empty padding.

    Shared text alone cannot identify a duplicate. Neither an inset child grid
    nor a strip cutting a merged cell may disappear through this path.
    """
    from types import SimpleNamespace
    from doc_understanding.parser.physical.nested_regions import native_partition_support
    nb, pb = native.source.bbox, physical.bounding_box
    if (abs(nb.x0-pb.x0)>1.5 or abs(nb.x1-pb.x1)>1.5
            or nb.y0<pb.y0-1.5 or nb.y1>pb.y1+1.5):
        return None
    intersecting = [c for c in physical.cells
                    if min(nb.y1,c.source_bbox.y1)-max(nb.y0,c.source_bbox.y0)>1.5]
    if not intersecting or any(c.source_bbox.y0<nb.y0-1.5 or c.source_bbox.y1>nb.y1+1.5
                               for c in intersecting):
        return None
    partition = native_partition_support(native, SimpleNamespace(cells=intersecting), nonblank, 1.5)
    if partition is None:
        return None
    return [(n, group['physical_cell_id']) for group in partition for n in group['native_cell_ids']]


# 원본 표 조각과 물리 표의 대응·보류 사유·공유 span을 기록하는 감사 모델이다.
class PartialRepresentation(EvidenceModel):
    native_table_id: str
    physical_table_id: str
    parent_cell_id: str | None
    status: Literal['redundant_representation', 'rejected', 'ambiguous']
    reasons: tuple[str, ...]
    cell_pairs: tuple[tuple[str, str], ...]
    shared_span_ids: tuple[str, ...]


# 원본 표의 부분 표현이 물리 표와 중복되는지 검사해 대응표와 전체 판정 기록을 반환한다.
# 동일 원본에 여러 완전 격자가 대응하면 ambiguous로 남긴다.
def partial_representations(page, physical, excluded_native):
    # Lazy import avoids a model dependency cycle. Same frozen containment rule.
    from doc_understanding.parser.assembly.nested_content import contains, choose_owner, same_box
    spans = {s.span_id:s for b in page.blocks for s in b.spans}
    nonblank = {s for s,v in spans.items() if v.text_raw.strip()}
    audit=[]
    for n in page.tables:
        if n.table_candidate_id in excluded_native or len(n.cells)<2:
            continue
        nr={s for c in n.cells for s in c.span_refs}; ns=nr & nonblank
        for p in physical:
            if p.page_number!=page.page_number or len(p.cells)<2:
                continue
            pr={s for c in p.cells for s in c.span_refs}; ps=pr & nonblank
            if not ns or not ns < ps or not overlap(n.source.bbox,p.bounding_box):
                continue
            reasons=set();pairs=[];nb=n.source.bbox;pb=p.bounding_box
            row_pairs=complete_row_partition(n,p,nonblank)
            if (nr|pr)-spans.keys():reasons.add('UNKNOWN_SOURCE_SPAN')
            if (abs(nb.y0-pb.y0)>1.5 or abs(nb.y1-pb.y1)>1.5
                    or nb.x0<pb.x0-1.5 or nb.x1>pb.x1+1.5) and row_pairs is None:
                reasons.add('NOT_FULL_HEIGHT_COLUMN_STRIP')
            for cells,getbox in [(n.cells,lambda c:c.source.bbox),(p.cells,lambda c:c.source_bbox)]:
                counts=Counter(s for c in cells for s in c.span_refs if s in nonblank)
                if any(v!=1 for v in counts.values()):reasons.add('DUPLICATE_CELL_SPAN_ASSIGNMENT')
                if any(not supports(getbox(c),spans[s]) for c in cells for s in set(c.span_refs)&nonblank):
                    reasons.add('SPAN_CELL_COORDINATE_CONFLICT')
            for c in (() if row_pairs is not None else n.cells):
                matches=[q for q in p.cells if same_box(c.source.bbox,q.source_bbox)
                         and (set(c.span_refs)&nonblank)==(set(q.span_refs)&nonblank)]
                if len(matches)!=1:reasons.add('CELL_IDENTITY_NOT_ONE_TO_ONE')
                else:pairs.append((c.cell_id,matches[0].cell_id))
            if row_pairs is not None:pairs=row_pairs
            mapped={b for _,b in pairs}
            if row_pairs is None and len(mapped)!=len(pairs):reasons.add('CELL_IDENTITY_NOT_ONE_TO_ONE')
            # Entire strip must be accounted for, including empty cells. Crossing
            # cells mean the candidate cut through a merged cell, not a duplicate.
            for c in (() if row_pairs is not None else p.cells):
                b=c.source_bbox
                if min(nb.x1,b.x1)-max(nb.x0,b.x0)>1.5:
                    if b.x0<nb.x0-1.5 or b.x1>nb.x1+1.5 or c.cell_id not in mapped:
                        reasons.add('INCOMPLETE_OR_CROSSING_STRIP')
            pool=[c for t in page.tables if t.table_candidate_id!=n.table_candidate_id for c in t.cells]
            no=choose_owner([c for c in pool if contains(nb,c.source.bbox)])
            po=choose_owner([c for c in pool if contains(pb,c.source.bbox)])
            owner=po if no==po else None
            if not owner:reasons.add('COMMON_UNIQUE_PARENT_MISSING')
            elif not pr.issubset(set(next(c for c in pool if c.cell_id==owner).span_refs)):
                reasons.add('CHILD_SPANS_OUTSIDE_PARENT')
            audit.append(PartialRepresentation(native_table_id=n.table_candidate_id,physical_table_id=p.table_id,
                parent_cell_id=owner,status='rejected' if reasons else 'redundant_representation',
                reasons=tuple(sorted(reasons)),cell_pairs=tuple(pairs),shared_span_ids=tuple(sorted(ns))))
    counts=Counter(a.native_table_id for a in audit if a.status=='redundant_representation')
    audit=[a.model_copy(update={'status':'ambiguous','reasons':('COMPETING_COMPLETE_GRIDS',)})
           if a.status=='redundant_representation' and counts[a.native_table_id]!=1 else a for a in audit]
    return {a.native_table_id:a.physical_table_id for a in audit if a.status=='redundant_representation'},tuple(audit)
