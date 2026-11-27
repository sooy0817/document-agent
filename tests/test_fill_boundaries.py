"""General geometry fixtures; no document names or expected answers in recovery."""
import pymupdf
from pathlib import Path
from doc_understanding.domain.contracts import ExtractionRequest
from doc_understanding.parser.extraction.pymupdf_adapter import PyMuPDFExtractor
from doc_understanding.parser.orchestration.workflow import extract_ir
from doc_understanding.parser.physical.physical_grid import PhysicalGridReconstructor
from doc_understanding.parser.physical.fill_boundaries import FillBoundaryDetector
from doc_understanding.parser.physical.physical_models import GridPolicy

def sample(tmp_path, fill=True, background=False):
    with pymupdf.open() as pdf:
        p=pdf.new_page(width=250,height=180)
        if background:p.draw_rect((30,30,210,150),color=None,fill=(.8,.8,.8))
        for y in (30,70,110,150):
            if fill:p.draw_rect((30,y-.25,210,y+.25),color=None,fill=(0,0,0))
            else:p.draw_line((30,y),(210,y))
        for x in (30,90,150,210):
            for y in range(30,150,5):
                if fill:p.draw_rect((x-.25,y,x+.25,y+5),color=None,fill=(0,0,0))
                else:p.draw_line((x,30),(x,150))
        for r in range(3):
            for c in range(3):p.insert_text((35+c*60,50+r*40),['', '0', '-7'][c],fontsize=9)
        ir=extract_ir(ExtractionRequest(pdf.tobytes(),'geometry.pdf'),PyMuPDFExtractor(),tmp_path)
    return ir

def test_fill_fragments_restore_cells_and_preserve_zero_negative_blank(tmp_path):
    ir=sample(tmp_path,background=True);page=ir.pages[0];before=ir.model_dump_json()
    base=PhysicalGridReconstructor().analyze_page(ir.document_id,page,mode='vector')
    assert not any(t.cells for t in base.tables)
    detector=FillBoundaryDetector(GridPolicy())
    result=PhysicalGridReconstructor(line_detector=detector).analyze_page(ir.document_id,page,mode='vector')
    table=max(result.tables,key=lambda t:len(t.cells))
    assert len(table.cells)==9
    assert {c.original_text for c in table.cells}=={'','0','-7'}
    assert all(c.status!='resolved' for c in table.cells)
    assert all(l.source.block_id and '/fill-center/' in l.line_id for l in table.lines)
    assert ir.model_dump_json()==before

def test_background_rectangle_is_not_four_boundaries(tmp_path):
    ir=sample(tmp_path,background=True);p=ir.pages[0]
    backgrounds=tuple(b for b in p.blocks if b.kind=='drawing' and b.source.bbox.x1-b.source.bbox.x0>100 and b.source.bbox.y1-b.source.bbox.y0>100)
    assert backgrounds
    assert not FillBoundaryDetector(GridPolicy()).vector(p.model_copy(update={'blocks':backgrounds}))

def test_stroke_path_is_unchanged(tmp_path):
    from doc_understanding.parser.physical.line_detector import LineDetector
    ir=sample(tmp_path,fill=False);p=ir.pages[0]
    assert FillBoundaryDetector(GridPolicy()).vector(p)==LineDetector(GridPolicy()).vector(p)


def test_document_batch_default_uses_fill_without_promoting_semantics(tmp_path):
    import json
    from doc_understanding.rag.batch import worker
    from doc_understanding.domain.pipeline_contracts import CanonicalDocument
    ir=sample(tmp_path/'seed',background=True)
    source=tmp_path/'seed'/str(ir.run.run_id)/'source.pdf'
    before=source.read_bytes()
    result=worker(source,tmp_path/'batch')
    doc=CanonicalDocument.model_validate_json(Path(result['document_path']).read_bytes())
    table=max(doc.physical_tables,key=lambda t:len(t.cells))
    assert len(table.cells)==9
    assert {c.original_text for c in table.cells}=={'','0','-7'}
    assert all(c.status!='resolved' for c in table.cells)
    assert not result['checks']['semantics_validated']
    assert result['checks']['facts_published']==0
    manifest=json.loads(Path(result['manifest']).read_text(encoding='utf8'))
    assert manifest['table_summary']['boundary_profile']=='stroke_fill'
    assert source.read_bytes()==before


def test_evaluator_legacy_stroke_profile_remains_available(tmp_path):
    from doc_understanding.parser.physical.runner import evaluate_pdf
    ir=sample(tmp_path/'seed')
    source=tmp_path/'seed'/str(ir.run.run_id)/'source.pdf'
    _,summary=evaluate_pdf(source,tmp_path/'legacy',focus_pages=(),boundary_profile='stroke')
    assert summary['boundary_profile']=='stroke'
    assert all(t['cells']==0 for t in summary['tables'])
