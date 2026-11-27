"""Continuation uses geometry and sequential evidence, never column count alone."""
from types import SimpleNamespace as NS

import pytest
from doc_understanding.domain.models import BBox, SourceRef
from doc_understanding.parser.physical.physical_models import PhysicalCell, GridBand
from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer
from doc_understanding.parser.relations.header_inheritance import inherit, effective_structure
from doc_understanding.parser.relations.logical_tables import validate_logical_links
from tests.semantic_fixtures import compound


def table(page,top,header,first,height=20):
    base=compound()[0];rows=[];cells=[]
    texts=([['Ordinal','Group','Reading']] if header else [])+[[str(i),'sample',str(i*10)] for i in range(first,first+4)]
    tid=f'laboratory/p{page}/table'
    for r,values in enumerate(texts):
        rows.append(GridBand(index=r,start=top+r*height,end=top+(r+1)*height))
        for c,text in enumerate(values):
            cid=f'{tid}/r{r}c{c}';box=BBox(x0=20+c*50,x1=70+c*50,y0=top+r*height,y1=top+(r+1)*height)
            cells.append(PhysicalCell(cell_id=cid,row_index=r,column_index=c,rowspan=1,colspan=1,original_text=text,
                source_bbox=box,source=SourceRef(document_id='laboratory',page_number=page,bbox=box,cell_id=cid),
                status='resolved',extraction_method='vector_grid',occupied_slots=((r,c),)))
    return base.model_copy(update=dict(table_id=tid,page_number=page,cells=tuple(cells),rows=tuple(rows),
        columns=tuple(GridBand(index=c,start=20+c*50,end=70+c*50) for c in range(3)),
        bounding_box=BBox(x0=20,y0=top,x1=170,y1=top+len(rows)*height)))


def case(change=None):
    a,b=table(1,680,True,1),table(2,40,False,5)
    if change=='different_table':b=table(2,40,False,1)
    if change=='boundary':b=b.model_copy(update={'columns':tuple(x.model_copy(update={'start':x.start+8,'end':x.end+8}) for x in b.columns)})
    if change=='not_end':a=table(1,250,True,1)
    if change=='unresolved':b=b.model_copy(update={'cells':tuple(x.model_copy(update={'status':'unresolved'}) if x.row_index==2 else x for x in b.cells)})
    ir=NS(pages=[NS(page_number=i,height=800,blocks=[]) for i in (1,2,3)])
    sa=SemanticTableAnalyzer().analyze(a,header_rows=1,stub_columns=1)
    sb=SemanticTableAnalyzer().analyze(b)
    if change=='weak_header':sa=SemanticTableAnalyzer().analyze(a)
    return ir,(a,b),(sa,sb)


def test_inherit_header_preserves_physical_and_records_every_value_source():
    ir,physical,structured=case();before=[t.model_dump_json() for t in physical]
    tables,links=inherit(ir,physical,structured)
    assert len(links)==1 and links[0].status=='accepted'
    assert tables[1].panels[0].header_rows==0 and len(tables[1].cell_mappings)==8
    assert all(m.evidence_state=='unresolved' for m in tables[1].cell_mappings)
    doc=NS(table_structure=tables,physical_tables=physical,logical_table_links=links,document_ir=ir)
    validate_logical_links(doc)
    view=effective_structure(doc,physical[1].table_id)
    assert all(m.column_path and m.evidence_state=='inferred' for m in view.cell_mappings)
    assert view.column_tree==structured[0].column_tree
    assert len(links[0].paths)==8 and all(p.column_source_cell_ids for p in links[0].paths)
    assert [t.model_dump_json() for t in physical]==before


def test_third_page_borrows_the_first_page_nodes_through_both_links():
    ir,physical,structured=case()
    a=physical[0];b=table(2,40,False,5,height=185);c=table(3,40,False,9)
    physical=(a,b,c)
    structured=(structured[0],SemanticTableAnalyzer().analyze(b),SemanticTableAnalyzer().analyze(c))
    tables,links=inherit(ir,physical,structured)
    assert [l.status for l in links]==['accepted','accepted']
    assert links[1].header_source_cell_ids==links[0].header_source_cell_ids
    assert links[1].source_table_id==b.table_id
    doc=NS(table_structure=tables,physical_tables=physical,logical_table_links=links,document_ir=ir)
    validate_logical_links(doc)
    assert effective_structure(doc,c.table_id).column_tree==structured[0].column_tree


def test_forged_accepted_link_cannot_bypass_sequence_evidence():
    ir,physical,structured=case('different_table')
    tables,links=inherit(ir,physical,structured)
    doc=NS(table_structure=tables,physical_tables=physical,document_ir=ir,
           logical_table_links=(links[0].model_copy(update={'status':'accepted'}),))
    with pytest.raises(ValueError,match='ACCEPTED_HEADER_LINK_WITHOUT_EVIDENCE'):
        validate_logical_links(doc)


@pytest.mark.parametrize('change,reason',[
    ('different_table','ORDINAL_SEQUENCE_NOT_CONTINUOUS'),('boundary','COLUMN_BOUNDARIES_DIFFER'),
    ('not_end','SOURCE_NOT_AT_PAGE_END'),('unresolved','NON_FLAT_OR_UNRESOLVED_GRID'),
    ('weak_header','SOURCE_HEADER_UNRESOLVED')])
def test_same_column_count_and_weak_evidence_remain_candidates(change,reason):
    ir,physical,structured=case(change)
    tables,links=inherit(ir,physical,structured)
    assert links and all(l.status=='candidate' for l in links)
    assert reason in links[0].reason_codes
    assert tables==structured
    doc=NS(table_structure=tables,logical_table_links=links)
    assert effective_structure(doc,physical[1].table_id)==structured[1]


@pytest.mark.parametrize("count,first,replacement,expected", [
    (2,5,None,"accepted"), (1,5,None,"candidate"), (2,8,None,"candidate"),
    (2,5,"same","candidate"), (2,5,"-","candidate"),
])
def test_short_continuation_needs_contiguous_ordinals_and_two_distinct_body_types(count,first,replacement,expected):
    ir, (a, _), _ = case()
    b = table(2,40,False,first)
    b = b.model_copy(update={"cells":tuple(c for c in b.cells if c.row_index<count),
                             "rows":b.rows[:count], "bounding_box":b.bounding_box.model_copy(
                                 update={"y1":b.rows[count-1].end})})
    def as_text(t):
        return t.model_copy(update={"cells":tuple(c.model_copy(update={"original_text":replacement})
            if c.column_index==2 and not (t is a and c.row_index==0) else c for c in t.cells)})
    if replacement is not None:
        a,b=as_text(a),as_text(b)
    sa=SemanticTableAnalyzer().analyze(a,header_rows=1,stub_columns=1)
    sb=SemanticTableAnalyzer().analyze(b)
    structs,links=inherit(ir,(a,b),(sa,sb))
    assert [l.status for l in links]==[expected]
    if expected=="accepted":
        assert len(links[0].paths)==4
        validate_logical_links(NS(table_structure=structs,physical_tables=(a,b),logical_table_links=links,document_ir=ir))


@pytest.mark.parametrize("other_top,expected", [(180,"accepted"), (675,"candidate")])
def test_competing_predecessor_must_reach_page_edge(other_top,expected):
    ir,(a,b),(sa,sb)=case()
    other=table(1,other_top,True,1)
    other=other.model_copy(update={"table_id":"laboratory/p1/other"})
    so=SemanticTableAnalyzer().analyze(other,header_rows=1,stub_columns=1)
    _,links=inherit(ir,(a,other,b),(sa,so,sb))
    assert [l.status for l in links]==[expected]
    if expected=="candidate":
        assert "COMPETING_PREDECESSORS" in links[0].reason_codes
