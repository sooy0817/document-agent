"""Geometry/source identity negatives; no document labels in production rules."""
from copy import deepcopy
from types import SimpleNamespace as NS
import pytest
from doc_understanding.domain.models import BBox
from doc_understanding.parser.assembly.nested_content import ContentItem, group_reading_order
from doc_understanding.parser.physical.partial_representation import partial_representations

def box(*v):
    return BBox(x0=v[0],y0=v[1],x1=v[2],y1=v[3])

def fixture():
    spans=[]; pc=[]; nc=[]
    for r in range(2):
        for c in range(3):
            sid=f's{r}{c}'; b=box(10+c*20,10+r*20,30+c*20,30+r*20)
            spans.append(NS(span_id=sid,text_raw='0',source=NS(bbox=box(b.x0+3,b.y0+3,b.x1-3,b.y1-3))))
            pc.append(NS(cell_id=f'p{r}{c}',span_refs=(sid,),source_bbox=b))
            if c==1:nc.append(NS(cell_id=f'n{r}{c}',span_refs=(sid,),source=NS(bbox=b)))
    n=NS(table_candidate_id='native-strip',cells=nc,source=NS(bbox=box(30,10,50,50)))
    parent=NS(cell_id='owner',span_refs=tuple(s.span_id for s in spans),source=NS(bbox=box(0,0,80,80)))
    outer=NS(table_candidate_id='outer',cells=[parent],source=NS(bbox=box(0,0,80,80)))
    p=NS(table_id='physical',cells=pc,bounding_box=box(10,10,70,50),page_number=1)
    return NS(page_number=1,tables=[outer,n],blocks=[NS(spans=spans)]),p

def test_full_height_strip_is_audited_without_mutating_source():
    page,p=fixture();before=deepcopy(page)
    aliases,audit=partial_representations(page,[p],set())
    assert aliases=={'native-strip':'physical'}
    assert audit[0].status=='redundant_representation'
    assert len(audit[0].cell_pairs)==2 and audit[0].parent_cell_id=='owner'
    assert page==before

@pytest.mark.parametrize('damage',['partial_row','wrong_cell','unknown','duplicate','competing','wrong_owner','inset'])
def test_subset_alone_never_removes_a_table(damage):
    page,p=fixture();ps=[p];n=page.tables[1]
    if damage=='partial_row':n.source.bbox=box(30,10,50,30);n.cells=n.cells[:1]
    if damage=='wrong_cell':n.cells[0].source.bbox=box(31.6,10,50,30)
    if damage=='unknown':n.cells[0].span_refs+=('unknown',)
    if damage=='duplicate':p.cells[0].span_refs+=('s01',)
    if damage=='competing':q=deepcopy(p);q.table_id='other';ps.append(q)
    if damage=='wrong_owner':page.tables[0].cells[0].span_refs=('s01','s11')
    if damage=='inset':n.source.bbox=box(32,12,48,48)
    aliases,_=partial_representations(page,ps,set())
    assert not aliases

def item(name,kind,b):
    return ContentItem(ref_id=name,kind=kind,bbox=box(*b),span_ids=(name,))

def span_for(i):
    return NS(source=NS(bbox=i.bbox),direction=(1,0))

def test_inline_text_keeps_ids_and_forms_one_line():
    items=[item('a','content',(0,0,20,10)),item('b','content',(25,0,80,10)),item('t','table',(0,20,80,60))]
    ordered,groups,status,reasons=group_reading_order(items,{i.ref_id:span_for(i) for i in items})
    assert status=='total' and not reasons
    assert groups[0].kind=='line' and groups[0].item_ref_ids==('a','b')
    assert len(ordered)==3

def test_parallel_tables_are_partial_not_failed_containment():
    items=[item('a','table',(0,0,20,50)),item('b','table',(30,0,50,50)),item('p','content',(0,60,50,70))]
    _,groups,status,reasons=group_reading_order(items,{})
    assert status=='partial' and groups[0].kind=='parallel'
    assert 'READING_ORDER_PARTIAL' in reasons

def test_overlapping_or_mixed_regions_remain_unresolved():
    items=[item('a','table',(0,0,30,50)),item('b','content',(20,0,50,50))]
    assert group_reading_order(items,{})[2]=='unresolved'

def test_far_apart_text_is_not_claimed_to_be_one_sentence():
    items=[item('a','content',(0,0,10,10)),item('b','content',(100,0,110,10))]
    assert group_reading_order(items,{i.ref_id:span_for(i) for i in items})[2]=='unresolved'


@pytest.mark.parametrize('key', ['P01', 'F05'])
def test_development_source_preservation_and_separate_statuses(key):
    import json
    from pathlib import Path
    from doc_understanding.domain.pipeline_contracts import CanonicalDocument
    from doc_understanding.parser.assembly.nested_content import build_nested_content, validate_links
    data=Path('data/nested-generalization/v1')
    rec=next(r for r in json.loads((data/'evaluation.json').read_text(encoding='utf8'))['documents'] if r['id']==key)
    doc=CanonicalDocument.model_validate_json((Path(rec['run'])/'document.json').read_bytes())
    before=doc.model_dump_json();links=build_nested_content(doc)
    assert validate_links(doc,links)['references_valid'] and before==doc.model_dump_json()
    if key=='P01':
        assert sum(a.status=='redundant_representation' for a in links.partial_representations)==1
        cells=[c for c in links.cells if c.page_number==14]
        assert len(cells)==2 and all(c.status=='geometrically_linked' for c in cells)
        assert all(not c.duplicate_span_ids and c.coverage_status=='complete' for c in cells)
    else:
        c=next(c for c in links.cells if c.page_number==2)
        assert c.containment_status=='linked' and c.coverage_status=='complete'
        assert c.reading_order_status=='partial' and c.status=='unresolved'
        assert any(g.kind=='parallel' and len(g.item_ref_ids)==4 for g in c.reading_groups)
