"""Conservative two-column aligned-span fallback when ruled-grid analysis is empty.

Only repeated single numeric spans with left-hand labels qualify. Complex numeric
columns, missing headers, wrapped/interleaved text and weak alignment abstain.
All numbers/text/coordinates come from existing DocumentIR spans. Original
PhysicalTableIR remains in its original artifact; this is an inferred view.
"""
from statistics import median

from doc_understanding.domain.models import BBox, Issue, SourceRef
from doc_understanding.table_understanding.physical_models import GridBand, PhysicalCell, SourceCellLink
from .index import parse_value


def aligned_two_column(page, original):
    if len(original.source_cells) < 4:
        return None
    box = original.bounding_box
    spans = [s for b in page.blocks for s in b.spans if s.text_raw.strip()
             and box.x0 <= (s.source.bbox.x0+s.source.bbox.x1)/2 <= box.x1
             and box.y0 <= (s.source.bbox.y0+s.source.bbox.y1)/2 <= box.y1]
    lines = []
    for s in sorted(spans,key=lambda s: ((s.source.bbox.y0+s.source.bbox.y1)/2,s.source.bbox.x0)):
        center=(s.source.bbox.y0+s.source.bbox.y1)/2
        match=next((line for line in reversed(lines) if abs(line[0]-center)<2),None)
        if match:
            match[1].append(s)
        else:
            lines.append([center,[s]])
    rows, numeric_rows = [], 0
    for _,line in lines:
        line.sort(key=lambda s:s.source.bbox.x0)
        numbers=[s for s in line if parse_value(s.text_raw)[0] in ('number','triangle_decrease_candidate')
                 and s.source.bbox.x0 > box.x0+(box.x1-box.x0)*.25]
        if numbers:
            numeric_rows+=1
        if len(numbers)!=1:
            continue
        value=numbers[0]
        label=[s for s in line if s.source.bbox.x1+10 < value.source.bbox.x0]
        if label and len(label)+1==len(line):
            rows.append((label,value))
    if len(rows)<3 or len(rows)/max(numeric_rows,1)<.8:
        return None
    right=median(v.source.bbox.x1 for _,v in rows)
    if any(abs(v.source.bbox.x1-right)>3 for _,v in rows):
        return None
    max_label=max(s.source.bbox.x1 for label,_ in rows for s in label)
    min_value=min(v.source.bbox.x0 for _,v in rows)
    if min_value-max_label < 12:
        return None
    split=(max_label+min_value)/2
    first_y=min(s.source.bbox.y0 for label,v in rows for s in label+[v])
    headers=[line for _,line in lines if max(s.source.bbox.y1 for s in line)<first_y]
    # At least one explicit right-side header is needed; do not invent a measure.
    if not headers or not any(any(s.source.bbox.x0>=split for s in line) for line in headers):
        return None
    body_centers=[(v.source.bbox.y0+v.source.bbox.y1)/2 for _,v in rows]
    # Unassigned/interleaved lines within the value rows may be wrapped labels/notes.
    if any(body_centers[0]+2 < center < body_centers[-1]-2 and
           min(abs(center-y) for y in body_centers)>2 for center,_ in lines):
        return None
    groups=[(line,None) for line in headers]+rows
    centers=[median((s.source.bbox.y0+s.source.bbox.y1)/2 for s in label+([value] if value else []))
             for label,value in groups]
    cuts=[box.y0]+[(a+b)/2 for a,b in zip(centers,centers[1:])]+[box.y1]
    cells=[]
    for ri,(label,value) in enumerate(groups):
        if value:
            parts=[(0,1,label),(1,1,[value])]
        else:
            left=[s for s in label if s.source.bbox.x0<split]
            right_spans=[s for s in label if s.source.bbox.x0>=split]
            if left and not right_spans:
                parts=[(0,2,left)]
            else:
                parts=[(ci,1,ss) for ci,ss in ((0,left),(1,right_spans)) if ss]
        for ci,colspan,ss in parts:
            sb=BBox(x0=min(s.source.bbox.x0 for s in ss),y0=min(s.source.bbox.y0 for s in ss),
                    x1=max(s.source.bbox.x1 for s in ss),y1=max(s.source.bbox.y1 for s in ss))
            cid=original.table_id+f'/text-span/r{ri}c{ci}'
            raw=' '.join(s.text_raw for s in sorted(ss,key=lambda s:s.source.bbox.x0))
            source=SourceRef(document_id=original.document_id,page_number=original.page_number,
                             bbox=sb,cell_id=cid)
            cells.append(PhysicalCell(cell_id=cid,row_index=ri,column_index=ci,rowspan=1,colspan=colspan,
                                       original_text=raw,source_bbox=sb,source=source,
                                       span_refs=tuple(s.span_id for s in ss),extraction_method='unresolved',
                                       status='inferred',occupied_slots=tuple((ri,c) for c in range(ci,ci+colspan))))
    issue=Issue(code='TEXT_ALIGNED_GRID_CANDIDATE',severity='warning',
                message='Derived two-column repeated numeric-span layout; original failed ruled-grid artifact retained. All cell geometry/semantics remain inferred.',
                source_references=original.source_references)
    result=original.model_copy(update=dict(
        rows=tuple(GridBand(index=i,start=a,end=b) for i,(a,b) in enumerate(zip(cuts,cuts[1:]))),
        columns=(GridBand(index=0,start=box.x0,end=split),GridBand(index=1,start=split,end=box.x1)),
        cells=tuple(cells),source_links=tuple(SourceCellLink(source_cell_id=c.cell_id,
                                                           reconstructed_cell_id=None,relation='unresolved') for c in original.source_cells),
        unassigned_source_cell_ids=tuple(c.cell_id for c in original.source_cells),
        boundaries=(),unresolved_boundaries=(),analysis_state='review_required',
        analyzer_version=original.analyzer_version+'+aligned-text/0.1',
        validation_issues=original.validation_issues+(issue,)))
    return result,len(headers)
