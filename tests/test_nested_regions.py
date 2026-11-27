"""General rejection invariants plus the declared development PDF; no external calls."""
import json
from pathlib import Path
import pytest
from doc_understanding.domain.models import DocumentIR
from doc_understanding.parser.physical.physical_models import PhysicalPageResult
from doc_understanding.parser.physical.nested_regions import split_nested_regions
from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer
from doc_understanding.parser.assembly.document import assemble, validate_document
from doc_understanding.parser.assembly.nested_content import build_nested_content,resolve_cell_content


@pytest.fixture(scope='module')
def source():
    manifest=json.loads(Path('data/financial-property-fill/v1/manifest.json').read_text(encoding='utf8'))
    record=next(x for x in manifest['documents'] if x['id']=='FP04')
    ir=DocumentIR.model_validate_json(Path(record['ir']).read_bytes())
    flat=[PhysicalPageResult.model_validate_json(Path(f'data/financial-property-fill/v1/runs/FP04/F0/page-{n:03}-physical.json').read_bytes()) for n in [1,2,3]]
    return ir,flat


def test_development_shared_border_parent_child_and_order(source):
    ir,flat=source;before=ir.model_dump_json();tables=[]
    for p,f in zip(ir.pages,flat):
        original=f.model_dump_json();r,log=split_nested_regions(ir.document_id,p,f)
        assert f.model_dump_json()==original
        assert log['applied']==(1 if p.page_number in [1,2] else 0)
        for c in log['candidates']:
            assert c['status']=='split_candidate'
            assert any(x.cell_id==c['parent_physical_cell_id'] for t in r.tables for x in t.cells)
            assert any(t.table_id==c['child_physical_table_id'] for t in r.tables)
        tables.extend(r.tables)
    doc=assemble(ir,tables,[SemanticTableAnalyzer().analyze(t) for t in tables])
    assert validate_document(doc)['reference_integrity']
    links=build_nested_content(doc)
    assert [c.status for c in links.cells]==['geometrically_linked']*2
    assert [[i.kind for i in c.items] for c in links.cells]==[['content']*3+['table'],['table']+['content']*2]
    assert all(not c.uncovered_span_ids and not c.duplicate_span_ids and not c.outside_parent_span_ids for c in links.cells)
    assert all(resolve_cell_content(doc,links,c.parent_cell_id) for c in links.cells)
    assert ir.model_dump_json()==before


def test_no_nested_candidates_leaves_page_identical(source):
    ir,flat=source
    page=ir.pages[0].model_copy(update={'tables':(ir.pages[0].tables[0],)})
    r,log=split_nested_regions(ir.document_id,page,flat[0])
    assert r is flat[0] and log['route']=='skip_no_candidate'


def test_competing_native_parents_are_not_forced(source):
    ir,flat=source;p=ir.pages[0];outer=p.tables[0]
    duplicate=outer.model_copy(update={'table_candidate_id':outer.table_candidate_id+'/duplicate',
        'cells':tuple(c.model_copy(update={'cell_id':c.cell_id+'/duplicate',
            'source':c.source.model_copy(update={'cell_id':c.cell_id+'/duplicate'})}) for c in outer.cells)})
    p=p.model_copy(update={'tables':p.tables+(duplicate,)})
    r,log=split_nested_regions(ir.document_id,p,flat[0])
    assert not log['applied']
    assert any('COMPETING_PARENT_CELLS' in c['reasons'] for c in log['candidates'])
    assert r.tables==flat[0].tables


def test_without_explanatory_text_child_layout_is_not_assumed(source):
    ir,flat=source;p=ir.pages[0];outer,child=p.tables
    child_spans=tuple(s for c in child.cells for s in c.span_refs)
    outer=outer.model_copy(update={'cells':tuple(c.model_copy(update={'span_refs':child_spans}) for c in outer.cells)})
    p=p.model_copy(update={'tables':(outer,child)})
    r,log=split_nested_regions(ir.document_id,p,flat[0])
    assert not log['applied'] and r.tables==flat[0].tables
    assert any('INDEPENDENT_CHILD_LAYOUT_NOT_ESTABLISHED' in c['reasons'] for c in log['candidates'])


def test_missing_boundaries_retain_original_flat_result(source):
    ir,flat=source;f=flat[0].model_copy(update={'tables':tuple(t.model_copy(update={'lines':()}) for t in flat[0].tables)})
    r,log=split_nested_regions(ir.document_id,ir.pages[0],f)
    assert not log['applied'] and r.tables==f.tables
    assert log['candidates'][0]['reasons']


def test_budget_is_a_visible_abstention(source):
    ir,flat=source;r,log=split_nested_regions(ir.document_id,ir.pages[0],flat[0],max_candidates=0)
    assert r is flat[0] and not log['applied']
    assert log['candidates'][0]['reasons']==['NESTED_CANDIDATE_BUDGET_EXCEEDED']


@pytest.mark.parametrize('filled,scale,edge',[(False,1.0,'bottom'),(True,1.4,'bottom'),
                                          (False,1.0,'right'),(True,1.4,'right')])
def test_synthetic_nested_grid_with_shared_bottom(tmp_path,filled,scale,edge):
    import pymupdf
    from doc_understanding.domain.contracts import ExtractionRequest
    from doc_understanding.parser.extraction.pymupdf_adapter import PyMuPDFExtractor
    from doc_understanding.parser.orchestration.workflow import extract_ir
    from doc_understanding.parser.physical.fill_boundaries import FillBoundaryDetector
    from doc_understanding.parser.physical.physical_models import GridPolicy
    from doc_understanding.parser.physical.physical_grid import PhysicalGridReconstructor
    with pymupdf.open() as pdf:
        page=pdf.new_page(width=330*scale,height=280*scale)
        def line(x0,y0,x1,y1):
            if filled:
                page.draw_rect((x0*scale-.2,y0*scale-.2,x1*scale+.2,y1*scale+.2),color=None,fill=(0,0,0))
            else:page.draw_line((x0*scale,y0*scale),(x1*scale,y1*scale))
        for y in [20,50,220,250]:line(20,y,300,y)
        for x in [20,100,300]:line(x,20,x,250)
        right,bottom=(300,210) if edge=='right' else (285,220)
        for y in [130,175,bottom]:line(115,y,right,y)
        for x in [115,200,right]:line(x,130,x,bottom)
        for x,y,text in [(25,40,'Group'),(105,40,'Details'),(25,100,'Category'),(105,85,'Explanation before'),
                         (120,155,'Item'),(205,155,'Value'),(120,200,'A'),(205,200,'0'),(25,240,'End'),(105,240,'Footer')]:
            page.insert_text((x*scale,y*scale),text,fontsize=8*scale)
        ir=extract_ir(ExtractionRequest(pdf.tobytes(),'synthetic.pdf'),PyMuPDFExtractor(),tmp_path)
    policy=GridPolicy();p=ir.pages[0]
    flat=PhysicalGridReconstructor(policy,line_detector=FillBoundaryDetector(policy)).analyze_page(ir.document_id,p,mode='vector')
    result,log=split_nested_regions(ir.document_id,p,flat)
    assert log['applied']==1,log
    child=next(t for t in result.tables if t.table_id==log['candidates'][0]['child_physical_table_id'])
    assert len(child.cells)==4
    assert {c.original_text.strip() for c in child.cells}=={'Item','Value','A','0'}
