from types import SimpleNamespace

from doc_understanding.domain.models import BBox, SourceRef, TextSpanIR
from doc_understanding.parser.structure.text_grid import aligned_two_column
from tests.support import sample_ir


def table_fixture():
    from doc_understanding.parser.physical.physical_models import GridPolicy, PhysicalTableIR
    from uuid import uuid4
    ir=sample_ir()
    return PhysicalTableIR(table_id='t',document_id=ir.document_id,analysis_run_id=uuid4(),
                           source_ir_sha256='a'*64,page_number=1,bounding_box=BBox(x0=0,y0=0,x1=100,y1=100),
                           rows=(),columns=(),cells=(),source_cells=ir.pages[0].tables[0].cells,
                           source_links=(),page_refs=(1,),source_references=(),lines=(),intersections=(),
                           boundaries=(),unresolved_boundaries=(),policy=GridPolicy())


def page_fixture(header=True,extra_value=False,shift=False):
    spans=[]
    def span(text,x0,x1,y):
        sid=f's{len(spans)}'
        box=BBox(x0=x0,x1=x1,y0=y,y1=y+5)
        spans.append(TextSpanIR(span_id=sid,source=SourceRef(document_id=sample_ir().document_id,
                                                             page_number=1,bbox=box,span_id=sid),
                                text_raw=text,line_index=len(spans),font='fixture',font_size=5,flags=0))
    if header:
        span('Measure',70,90,5)
    for i,label in enumerate(('North','South','West')):
        span(label,3,30,25+i*20)
        span(str(i),82+(i*5 if shift else 0),90+(i*5 if shift else 0),25+i*20)
        if extra_value:
            span('17',50,60,25+i*20)
    return SimpleNamespace(blocks=[SimpleNamespace(spans=spans)])


def test_generic_repeated_numeric_alignment_preserves_zero_and_sources():
    result=aligned_two_column(page_fixture(),table_fixture())
    assert result
    table,header_count=result
    assert header_count==1
    assert [c.original_text for c in table.cells]==['Measure','North','0','South','1','West','2']
    assert all(c.status=='inferred' and c.span_refs for c in table.cells)
    assert table.analysis_state=='review_required'


def test_missing_headers_multiple_numeric_columns_and_alignment_conflicts_abstain():
    for kwargs in ({'header':False},{'extra_value':True},{'shift':True}):
        assert aligned_two_column(page_fixture(**kwargs),table_fixture()) is None
