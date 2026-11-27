"""Source-style evidence tests, independent of bank names and expected financial answers."""
from types import SimpleNamespace
from doc_understanding.domain.models import BBox, SourceRef, BlockIR, DrawingCommand, TextSpanIR
from doc_understanding.parser.physical.physical_models import PhysicalCell, GridBand
from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer
from tests.semantic_fixtures import compound


def sample(style='fill', header=('Category', 'Observation'), values=('13%', '27%')):
    base=compound()[0];cells=[];blocks=[]
    for r in range(3):
        for col in range(2):
            cid=f'r{r}c{col}';box=BBox(x0=col*120,y0=r*30,x1=(col+1)*120,y1=(r+1)*30)
            text=header[col] if r==0 else (f'Group {r}' if col==0 else values[r-1])
            ref=SourceRef(document_id='laboratory',page_number=1,bbox=box,cell_id=cid)
            sr=ref.model_copy(update={'cell_id':None,'block_id':cid+'/text','span_id':cid+'/span'})
            span=TextSpanIR(span_id=sr.span_id,source=sr,text_raw=text,line_index=0,font='Generic',font_size=10,flags=16 if style=='bold' and r==0 else 0)
            blocks.append(BlockIR(block_id=sr.block_id,kind='text',source=sr.model_copy(update={'span_id':None}),extraction_order=len(blocks),spans=(span,)))
            cells.append(PhysicalCell(cell_id=cid,row_index=r,column_index=col,rowspan=1,colspan=1,original_text=text,source_bbox=box,source=ref,span_refs=(sr.span_id,),status='resolved',extraction_method='vector_grid',occupied_slots=((r,col),)))
            if style in ('fill','all_fill') and (r==0 or style=='all_fill'):
                dr=ref.model_copy(update={'cell_id':None,'block_id':cid+'/fill'})
                blocks.append(BlockIR(block_id=dr.block_id,kind='drawing',source=dr,extraction_order=len(blocks),drawing_stroked=False,drawing_commands=(DrawingCommand(operator='re',coordinates=box.xyxy),)))
    t=base.model_copy(update={'cells':tuple(cells),'rows':tuple(GridBand(index=r,start=r*30,end=(r+1)*30) for r in range(3)),'columns':tuple(GridBand(index=c,start=c*120,end=(c+1)*120) for c in range(2)),'bounding_box':BBox(x0=0,y0=0,x1=240,y1=90)})
    page=SimpleNamespace(page_number=1,page_id='laboratory/p0001',blocks=tuple(blocks))
    return t,page


def test_source_fill_and_value_contrast_allow_inferred_paths_only():
    t,p=sample();before=t.model_dump_json()
    s=SemanticTableAnalyzer().analyze(t,source_page=p)
    assert all(m.evidence_state=='inferred' for m in s.cell_mappings)
    assert not any(a.code=='HEADER_REGION_AMBIGUOUS' for a in s.ambiguities)
    proof=next(i for i in s.issues if i.code=='SOURCE_HEADER_SUPPORTED')
    assert proof.source_references and 'fill_band' in proof.message
    assert s.independent_human_review=='pending' and s.analysis_state!='validated'
    assert t.model_dump_json()==before


def test_source_bold_contrast_supports_header_without_fill():
    t,p=sample('bold');s=SemanticTableAnalyzer().analyze(t,source_page=p)
    assert all(m.evidence_state=='inferred' for m in s.cell_mappings)


def test_missing_style_all_fill_or_numeric_first_row_do_not_clear_hold():
    for style,header,values in [('none',('Category','Observation'),('13%','27%')),('all_fill',('Category','Observation'),('13%','27%')),('fill',('1','2'),('13','27')),('fill',('A','B'),('C','D'))]:
        t,p=sample(style,header,values);s=SemanticTableAnalyzer().analyze(t,source_page=p)
        assert any(a.code=='HEADER_REGION_AMBIGUOUS' for a in s.ambiguities)
        assert all(m.evidence_state=='unresolved' for m in s.cell_mappings)


def test_wrong_page_or_missing_span_and_unresolved_physical_remain_held():
    t,p=sample();p.page_number=2
    assert all(m.evidence_state=='unresolved' for m in SemanticTableAnalyzer().analyze(t,source_page=p).cell_mappings)
    t,p=sample();p.blocks=tuple(b for b in p.blocks if b.kind!='text')
    assert all(m.evidence_state=='unresolved' for m in SemanticTableAnalyzer().analyze(t,source_page=p).cell_mappings)
    t,p=sample();t=t.model_copy(update={'cells':tuple(c.model_copy(update={'status':'unresolved'}) if c.row_index==1 else c for c in t.cells)})
    assert all(m.evidence_state=='unresolved' for m in SemanticTableAnalyzer().analyze(t,source_page=p).cell_mappings)


def test_unstyled_physical_only_interface_is_unchanged():
    t,p=sample()
    assert all(m.evidence_state=='unresolved' for m in SemanticTableAnalyzer().analyze(t).cell_mappings)


def test_source_object_order_is_not_reading_order_and_text_must_be_covered():
    t,p=sample('bold',('Group','Time range'))
    b=next(b for b in p.blocks if b.block_id=='r0c1/text');s=b.spans[0]
    fragments=tuple(s.model_copy(update={'span_id':s.span_id+str(i),'text_raw':txt,'source':s.source.model_copy(update={'span_id':s.span_id+str(i)})}) for i,txt in enumerate(['range','Time ','range']))
    p.blocks=tuple(b.model_copy(update={'spans':fragments}) if x==b else x for x in p.blocks)
    t=t.model_copy(update={'cells':tuple(c.model_copy(update={'span_refs':tuple(s.span_id for s in fragments)}) if c.cell_id=='r0c1' else c for c in t.cells)})
    assert all(m.evidence_state=='inferred' for m in SemanticTableAnalyzer().analyze(t,source_page=p).cell_mappings)
    t=t.model_copy(update={'cells':tuple(c.model_copy(update={'original_text':'unsupported label'}) if c.cell_id=='r0c1' else c for c in t.cells)})
    assert all(m.evidence_state=='unresolved' for m in SemanticTableAnalyzer().analyze(t,source_page=p).cell_mappings)
