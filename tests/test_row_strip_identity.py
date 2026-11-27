"""A duplicate row strip needs geometry, a complete partition and original spans."""
from copy import deepcopy
from types import SimpleNamespace as NS
import pytest
from doc_understanding.domain.models import BBox
from doc_understanding.parser.physical.partial_representation import partial_representations


def fixture():
    def box(a,b,c,d):return BBox(x0=a,y0=b,x1=c,y1=d)
    pc=[];nc=[];spans=[]
    for r in range(2):
        for c in range(2):
            b=box(10+40*c,10+20*r,50+40*c,30+20*r);sid=f's{r}{c}'
            pc.append(NS(cell_id=f'p{r}{c}',source_bbox=b,span_refs=(sid,)))
            spans.append(NS(span_id=sid,text_raw='same',source=NS(bbox=box(b.x0+10,b.y0+5,b.x1-10,b.y1-5))))
            if r==0:
                for i,(a,z,refs) in enumerate([(b.x0,b.x0+5,()),(b.x0+5,b.x1-5,(sid,)),(b.x1-5,b.x1,())]):
                    nc.append(NS(cell_id=f'n{c}{i}',source=NS(bbox=box(a,b.y0,z,b.y1)),span_refs=refs))
    n=NS(table_candidate_id='strip',source=NS(bbox=box(10,10,90,30)),cells=nc)
    parent=NS(cell_id='parent',source=NS(bbox=box(0,0,100,80)),span_refs=tuple(s.span_id for s in spans))
    outer=NS(table_candidate_id='outer',cells=[parent])
    p=NS(table_id='physical',bounding_box=box(10,10,90,50),page_number=1,cells=pc)
    return NS(page_number=1,blocks=[NS(spans=spans)],tables=[outer,n]),p,box


def test_row_padding_partition_preserves_all_native_fragments():
    page,p,_=fixture();before=deepcopy(page)
    aliases,audit=partial_representations(page,[p],set())
    assert aliases=={'strip':'physical'}
    assert len(audit[0].cell_pairs)==6
    assert page==before


@pytest.mark.parametrize('damage',['gap','inset','crossing_merge','different_source','unknown','duplicate','missing_blank','competing','parent_span'])
def test_real_child_or_incomplete_strip_is_never_suppressed(damage):
    page,p,box=fixture();n=page.tables[1];physical=[p]
    if damage=='gap':n.cells[0].source.bbox=box(10,10,12,30)
    if damage=='inset':n.source.bbox=box(12,10,88,30)
    if damage=='crossing_merge':p.cells[0].source_bbox=box(10,10,50,50)
    if damage=='different_source':n.cells[1].span_refs=('s10',)
    if damage=='unknown':n.cells[1].span_refs+=('foreign',)
    if damage=='duplicate':n.cells[0].span_refs=('s00',)
    if damage=='missing_blank':n.cells.pop(0)
    if damage=='competing':q=deepcopy(p);q.table_id='other';physical.append(q)
    if damage=='parent_span':page.tables[0].cells[0].span_refs=('s00','s01')
    assert not partial_representations(page,physical,set())[0]
