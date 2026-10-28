"""Repeated numeric/text alignment proposals; inferred gutters, no synthetic strokes."""
import re
from collections import defaultdict
from uuid import uuid4

from doc_understanding.domain.models import BBox, Issue, SourceRef
from doc_understanding.table_understanding.geometry import overlap, close_box, cluster
from doc_understanding.table_understanding.physical_models import (
    Boundary, GridBand, GridPolicy, PhysicalCell, PhysicalTableIR, SourceCellLink)
from doc_understanding.table_understanding.physical_validator import PhysicalStructureValidator
from doc_understanding.table_understanding.boundary_reconstructor import CellBoundaryReconstructor
from doc_understanding.table_understanding.intersection_analyzer import GridGeometry


def numeric(s):
    return bool(re.fullmatch(r'[−+\-△]?\(?\d[\d,]*(?:\.\d+)?\)?(?:%|\*{1,3})?',s.strip()))


def text_rows(page, box):
    spans = [s for b in page.blocks for s in b.spans if s.text_raw.strip()
             and box.x0 <= (s.source.bbox.x0+s.source.bbox.x1)/2 <= box.x1
             and box.y0 <= (s.source.bbox.y0+s.source.bbox.y1)/2 <= box.y1]
    rows = []
    for s in sorted(spans,key=lambda s: ((s.source.bbox.y0+s.source.bbox.y1)/2,s.source.bbox.x0)):
        y=(s.source.bbox.y0+s.source.bbox.y1)/2
        if rows and abs(y-rows[-1][0]) <= max(2,s.font_size*.35):
            rows[-1][1].append(s)
        else:
            rows.append([y,[s]])
    return rows


def text_regions(page, lines):
    boxes = [t.source.bbox for t in page.tables]
    groups = defaultdict(list)
    for line in lines:
        if line.axis=='horizontal' and line.end-line.start > 60:
            groups[(round(line.start/4),round(line.end/4))].append(line)
    for group in groups.values():
        group.sort(key=lambda line:line.position)
        runs=[]
        for line in group:
            if not runs or line.position-runs[-1][-1].position>90:
                runs.append([])
            runs[-1].append(line)
        for run in runs:
            if len(run)<2:
                continue
            box=BBox(x0=min(x.start for x in run),x1=max(x.end for x in run),
                     y0=min(x.position for x in run),y1=max(x.position for x in run))
            if box.y1-box.y0<10:
                continue
            rows=text_rows(page,box)
            # Open-bottom tables: extend through nearby numeric rows only.
            below=text_rows(page,BBox(x0=box.x0,x1=box.x1,y0=box.y1,y1=min(page.height,box.y1+70)))
            end=box.y1
            for y,sp in below:
                if y-end>20 or not any(numeric(s.text_raw) for s in sp):
                    break
                end=max(s.source.bbox.y1 for s in sp)+1
            if sum(any(numeric(s.text_raw) for s in ss) for _,ss in rows)>=2 or end>box.y1:
                boxes.append(box.model_copy(update={'y1':end}))
    # No rules: require a run of aligned, numeric-bearing native rows.
    rows=text_rows(page,BBox(x0=0,y0=0,x1=page.width,y1=page.height))
    runs=[]
    for y,sp in rows:
        ns=[s for s in sp if numeric(s.text_raw)]
        if len(ns)<2 or sum(len(s.text_raw) for s in sp)>180:
            continue
        if not runs or y-runs[-1][-1][0]>24 or abs(ns[-1].source.bbox.x1-runs[-1][-1][2])>15:
            runs.append([])
        runs[-1].append((y,sp,ns[-1].source.bbox.x1))
    for run in runs:
        if len(run)<3:
            continue
        ss=[s for _,sp,_ in run for s in sp]
        box=BBox(x0=min(s.source.bbox.x0 for s in ss)-2,x1=max(s.source.bbox.x1 for s in ss)+2,
                 y0=max(0,min(s.source.bbox.y0 for s in ss)-25),y1=max(s.source.bbox.y1 for s in ss)+2)
        boxes.append(box)
    unique=[]
    for box in boxes:
        if not any(overlap(box,b)>.8*min((b.x1-b.x0)*(b.y1-b.y0),(box.x1-box.x0)*(box.y1-box.y0)) for b in unique):
            unique.append(box)
    return unique


def reconstruct_text(document_id,page,box,index):
    rows=text_rows(page,box)
    if len(rows)<3:
        return None,'INSUFFICIENT_TEXT_ROWS'
    numbers=[(r,s) for r,(_,sp) in enumerate(rows) for s in sp if numeric(s.text_raw)]
    if len({r for r,_ in numbers})<3:
        return None,'INSUFFICIENT_NUMERIC_ROWS'
    anchors=cluster([s.source.bbox.x1 for _,s in numbers],3)
    anchors=[x for x in anchors if len({r for r,s in numbers if abs(s.source.bbox.x1-x)<=3})>=3]
    if len(anchors)<2:
        return None,'INSUFFICIENT_REPEATED_COLUMNS'
    groups=[[s for _,s in numbers if abs(s.source.bbox.x1-x)<=3] for x in anchors]
    extents=[(min(s.source.bbox.x0 for s in g),max(s.source.bbox.x1 for s in g)) for g in groups]
    leftlabels=[s for _,sp in rows for s in sp if s.source.bbox.x1<extents[0][0]-3]
    if not leftlabels:
        return None,'NO_DISTINCT_STUB'
    stub_end=max(s.source.bbox.x1 for s in leftlabels)
    xs=[box.x0,(stub_end+extents[0][0])/2]
    for a,b in zip(extents,extents[1:]):
        if b[0]-a[1]<3:
            return None,'OVERLAPPING_COLUMN_HYPOTHESES'
        xs.append((a[1]+b[0])/2)
    xs.append(box.x1)
    ys=[box.y0]+[(a[0]+b[0])/2 for a,b in zip(rows,rows[1:])]+[box.y1]
    if any(b<=a for a,b in zip(xs,xs[1:])) or any(b<=a for a,b in zip(ys,ys[1:])):
        return None,'NONMONOTONE_GRID'
    allsp=[s for _,sp in rows for s in sp]
    boundaries=[]
    for axis,pos,var in [('horizontal',ys,xs),('vertical',xs,ys)]:
        for i,p in enumerate(pos):
            for j,(a,b) in enumerate(zip(var,var[1:])):
                conflicts=[s for s in allsp if (
                    s.source.bbox.x0+.75<p<s.source.bbox.x1-.75 and a< (s.source.bbox.y0+s.source.bbox.y1)/2 <b
                    if axis=='vertical' else
                    False)]
                row,col=(i,j) if axis=='horizontal' else (j,i)
                boundaries.append(Boundary(boundary_id=f'{axis[0]}{row}-{col}',axis=axis,row=row,column=col,
                    position=p,start=a,end=b,coverage=0,status='absent' if conflicts else 'inferred',
                    line_ids=(),evidence_level='missing',text_conflict_span_ids=tuple(s.span_id for s in conflicts)))
    grid=GridGeometry(tuple(xs),tuple(ys),(),tuple(boundaries))
    tid=f'{page.page_id}/textphysical{index:03}'
    native=[t for t in page.tables if overlap(t.source.bbox,box)>0]
    sources=tuple(c for t in native for c in t.cells if overlap(c.source.bbox,box)>0)
    cells=[]
    links=[]
    for i,c in enumerate(CellBoundaryReconstructor().reconstruct(grid)):
        cb=BBox(x0=xs[c.column_start],x1=xs[c.column_end],y0=ys[c.row_start],y1=ys[c.row_end])
        spans=sorted([s for s in allsp if cb.x0<=(s.source.bbox.x0+s.source.bbox.x1)/2<cb.x1
                      and cb.y0<=(s.source.bbox.y0+s.source.bbox.y1)/2<cb.y1],key=lambda s:(s.source.bbox.y0,s.source.bbox.x0))
        ids=tuple(s.cell_id for s in sources if overlap(s.source.bbox,cb)>0)
        exact=[s for s in sources if close_box(s.source.bbox,cb,.75)]
        text=exact[0].text_raw if len(exact)==1 else '\n'.join(s.text_raw for s in spans)
        cid=f'{tid}/cell{i:04}'
        cells.append(PhysicalCell(cell_id=cid,row_index=c.row_start,column_index=c.column_start,
            rowspan=c.row_end-c.row_start if c.resolved else None,colspan=c.column_end-c.column_start if c.resolved else None,
            original_text=text,source_bbox=cb,source=SourceRef(document_id=document_id,page_number=page.page_number,bbox=cb,cell_id=cid),
            source_cell_ids=ids,span_refs=tuple(s.span_id for s in spans),extraction_method='unresolved',
            status='inferred' if c.resolved else 'unresolved',boundary_ids=c.boundary_ids,occupied_slots=c.slots))
        links.extend(SourceCellLink(source_cell_id=s,reconstructed_cell_id=cid,relation='overlap') for s in ids)
    unassigned=tuple(s.cell_id for s in sources if not any(link.source_cell_id==s.cell_id for link in links))
    links.extend(SourceCellLink(source_cell_id=s,reconstructed_cell_id=None,relation='unresolved') for s in unassigned)
    table=PhysicalTableIR(table_id=tid,document_id=document_id,page_number=page.page_number,analysis_run_id=uuid4(),
        analyzer_version='text-alignment-candidate/0.1',policy=GridPolicy(),source_ir_sha256=page.evidence_sha256,
        bounding_box=box,rows=tuple(GridBand(index=i,start=a,end=b) for i,(a,b) in enumerate(zip(ys,ys[1:]))),
        columns=tuple(GridBand(index=i,start=a,end=b) for i,(a,b) in enumerate(zip(xs,xs[1:]))),
        cells=tuple(cells),source_cells=sources,source_links=tuple(links),unassigned_source_cell_ids=unassigned,
        table_candidate_ids=tuple(t.table_candidate_id for t in native),page_refs=(page.page_number,),
        source_references=(SourceRef(document_id=document_id,page_number=page.page_number,bbox=box),),
        crop_refs=(page.image,),lines=(),intersections=(),boundaries=tuple(boundaries),
        unresolved_boundaries=tuple(b for b in boundaries if b.status=='inferred'),
        validation_issues=(Issue(code='TEXT_ALIGNMENT_BOUNDARY_CANDIDATE',severity='warning',
            message='Repeated text alignment proposes gutters; no observed grid strokes; semantic review required.'),))
    return PhysicalStructureValidator().validate(table,page),'TEXT_ALIGNMENT_CANDIDATE'
