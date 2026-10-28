"""Source identity between native and reconstructed tables, not semantic truth.

No string similarity, document labels, expected grid size or expanded point tolerance.
Exact nonblank span identity, spatial support and mutual uniqueness are required.
"""
from collections import Counter
from typing import Literal
from doc_understanding.domain.models import EvidenceModel


class TableIdentity(EvidenceModel):
    native_table_id: str
    physical_table_id: str
    status: Literal['matched', 'rejected', 'ambiguous']
    reasons: tuple[str, ...]
    shared_span_ids: tuple[str, ...]
    native_only_span_ids: tuple[str, ...]
    physical_only_span_ids: tuple[str, ...]
    unknown_span_ids: tuple[str, ...]
    unsupported_span_ids: tuple[str, ...]
    native_bbox_coverage: float
    physical_bbox_coverage: float


def overlap(a,b):
    return max(0,min(a.x1,b.x1)-max(a.x0,b.x0))*max(0,min(a.y1,b.y1)-max(a.y0,b.y0))


def area(b):return max(0,b.x1-b.x0)*max(0,b.y1-b.y0)


def supports(box,span,tolerance=1.5):
    b=span.source.bbox;x=(b.x0+b.x1)/2;y=(b.y0+b.y1)/2
    return box.x0-tolerance<=x<=box.x1+tolerance and box.y0-tolerance<=y<=box.y1+tolerance


def match_tables(page,physical):
    """Return only mutually unique edges and a full audit of spatial/span candidates.

    Coverage >= .8 in BOTH directions prevents a surrounding table from matching
    a tiny child even when it has no additional text. This is an area check, not
    permission to join by geometry alone. Point tolerance stays at 1.5.
    """
    spans={s.span_id:s for b in page.blocks for s in b.spans}
    nonblank={s for s,v in spans.items() if v.text_raw.strip()}
    audit=[]
    for n in page.tables:
        if len(n.cells)<2:continue
        nr={s for c in n.cells for s in c.span_refs};ns=nr&nonblank
        for p in physical:
            if p.page_number!=page.page_number or len(p.cells)<2:continue
            pr={s for c in p.cells for s in c.span_refs};ps=pr&nonblank
            common=ns&ps;inter=overlap(n.source.bbox,p.bounding_box)
            if not inter and not common:continue
            nc=inter/max(area(n.source.bbox),1e-9);pc=inter/max(area(p.bounding_box),1e-9)
            reasons=[];unknown=(nr|pr)-spans.keys();bad=set()
            if unknown:reasons.append('UNKNOWN_SOURCE_SPAN')
            if not ns or not ps:reasons.append('NO_NONBLANK_SPAN_SUPPORT')
            if ns!=ps:reasons.append('SPAN_SET_MISMATCH')
            if min(nc,pc)<.8:reasons.append('TABLE_FOOTPRINT_CONFLICT')
            for table,cells,getbox,outer in [(n,n.cells,lambda c:c.source.bbox,n.source.bbox),
                                            (p,p.cells,lambda c:c.source_bbox,p.bounding_box)]:
                counts=Counter(s for c in cells for s in c.span_refs if s in nonblank)
                if any(v!=1 for v in counts.values()):reasons.append('DUPLICATE_CELL_SPAN_ASSIGNMENT')
                for cell in cells:
                    for sid in set(cell.span_refs)&nonblank:
                        if not supports(getbox(cell),spans[sid]) or not supports(outer,spans[sid]):bad.add(sid)
            if bad:reasons.append('SPAN_CELL_COORDINATE_CONFLICT')
            audit.append(TableIdentity(native_table_id=n.table_candidate_id,physical_table_id=p.table_id,
                status='rejected' if reasons else 'matched',reasons=tuple(sorted(set(reasons))),
                shared_span_ids=tuple(sorted(common)),native_only_span_ids=tuple(sorted(ns-ps)),
                physical_only_span_ids=tuple(sorted(ps-ns)),unknown_span_ids=tuple(sorted(unknown)),
                unsupported_span_ids=tuple(sorted(bad)),native_bbox_coverage=nc,physical_bbox_coverage=pc))
    viable=[a for a in audit if a.status=='matched']
    natives=Counter(a.native_table_id for a in viable);physical_ids=Counter(a.physical_table_id for a in viable)
    audit=[a.model_copy(update={'status':'ambiguous','reasons':('TABLE_IDENTITY_NOT_ONE_TO_ONE',)})
           if a.status=='matched' and (natives[a.native_table_id]!=1 or physical_ids[a.physical_table_id]!=1) else a for a in audit]
    matches={a.native_table_id:(a.physical_table_id,) for a in audit if a.status=='matched'}
    return matches,tuple(audit)
