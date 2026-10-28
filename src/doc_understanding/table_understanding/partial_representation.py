"""Audit a complete column strip represented twice; never edit either source IR.

Subset spans alone are insufficient. Every native cell must coincide with one
complete physical cell, with identical source IDs, across the full grid height.
This deliberately excludes inset/partial-height nested grids.
"""
from collections import Counter
from typing import Literal
from doc_understanding.domain.models import EvidenceModel
from .table_identity import supports, overlap


class PartialRepresentation(EvidenceModel):
    native_table_id: str
    physical_table_id: str
    parent_cell_id: str | None
    status: Literal['redundant_representation', 'rejected', 'ambiguous']
    reasons: tuple[str, ...]
    cell_pairs: tuple[tuple[str, str], ...]
    shared_span_ids: tuple[str, ...]


def partial_representations(page, physical, excluded_native):
    # Lazy import avoids a model dependency cycle. Same frozen containment rule.
    from doc_understanding.document_rag.nested_content import contains, choose_owner, same_box
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
            if (nr|pr)-spans.keys():reasons.add('UNKNOWN_SOURCE_SPAN')
            if (abs(nb.y0-pb.y0)>1.5 or abs(nb.y1-pb.y1)>1.5
                    or nb.x0<pb.x0-1.5 or nb.x1>pb.x1+1.5):
                reasons.add('NOT_FULL_HEIGHT_COLUMN_STRIP')
            for cells,getbox in [(n.cells,lambda c:c.source.bbox),(p.cells,lambda c:c.source_bbox)]:
                counts=Counter(s for c in cells for s in c.span_refs if s in nonblank)
                if any(v!=1 for v in counts.values()):reasons.add('DUPLICATE_CELL_SPAN_ASSIGNMENT')
                if any(not supports(getbox(c),spans[s]) for c in cells for s in set(c.span_refs)&nonblank):
                    reasons.add('SPAN_CELL_COORDINATE_CONFLICT')
            for c in n.cells:
                matches=[q for q in p.cells if same_box(c.source.bbox,q.source_bbox)
                         and (set(c.span_refs)&nonblank)==(set(q.span_refs)&nonblank)]
                if len(matches)!=1:reasons.add('CELL_IDENTITY_NOT_ONE_TO_ONE')
                else:pairs.append((c.cell_id,matches[0].cell_id))
            mapped={b for _,b in pairs}
            if len(mapped)!=len(pairs):reasons.add('CELL_IDENTITY_NOT_ONE_TO_ONE')
            # Entire strip must be accounted for, including empty cells. Crossing
            # cells mean the candidate cut through a merged cell, not a duplicate.
            for c in p.cells:
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
