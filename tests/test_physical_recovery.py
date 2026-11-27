import pymupdf

from doc_understanding.domain.contracts import ExtractionRequest
from doc_understanding.parser.extraction.pymupdf_adapter import PyMuPDFExtractor
from doc_understanding.parser.orchestration.workflow import extract_ir
from doc_understanding.parser.physical.physical_grid import PhysicalGridReconstructor
from doc_understanding.experiments.physical_recovery.recovery import RecoveryAnalyzer, Strategy, needs_fill_raster


def fixture(tmp_path, ruled=True, fill=False):
    with pymupdf.open() as pdf:
        p=pdf.new_page(width=300,height=200)
        for r in range(6):
            for c in range(4):
                text=['Item','A','B','C'][c] if r==0 else (f'Group{r}' if c==0 else str(r*10+c))
                p.insert_text((32+c*50,38+r*10),text,fontsize=6)
        if ruled:
            for r in range(7):
                if fill:
                    p.draw_rect(pymupdf.Rect(30,30+r*10,230,31+r*10),color=None,fill=(0,0,0))
                else:
                    p.draw_line((30,30+r*10),(230,30+r*10))
            for c in range(5):
                for r in range(6):
                    p.draw_line((30+c*50,30+r*10),(30+c*50,40+r*10))
        ir=extract_ir(ExtractionRequest(pdf.tobytes(),'independent-grid.pdf'),PyMuPDFExtractor(),tmp_path)
    page=ir.pages[0]
    return ir,page,PhysicalGridReconstructor().analyze_page(ir.document_id,page)


def test_short_fragments_restore_source_cells_without_input_mutation(tmp_path):
    ir,page,base=fixture(tmp_path)
    before=ir.model_dump_json()
    result,log=RecoveryAnalyzer().analyze(ir.document_id,page,Strategy(short=True),base)
    assert log['short_fragments']>0
    assert any(len(t.columns)==4 and any(c.original_text=='31' for c in t.cells) for t in result.tables)
    assert all(c.status!='resolved' for t in result.tables for c in t.cells)
    assert ir.model_dump_json()==before


def test_borderless_text_candidates_retain_values_and_inferred_evidence(tmp_path):
    ir,page,base=fixture(tmp_path,ruled=False)
    result,_=RecoveryAnalyzer().analyze(ir.document_id,page,Strategy(text=True),base)
    assert result.tables
    assert any(c.original_text=='31' for t in result.tables for c in t.cells)
    assert all(t.analysis_state!='validated' for t in result.tables)
    assert all(not t.lines for t in result.tables)
    assert all(c.status!='resolved' for t in result.tables for c in t.cells)


def test_fill_fallback_predicate_is_shared_and_baseline_stays_identity(tmp_path):
    ir,page,base=fixture(tmp_path,fill=True)
    assert needs_fill_raster(page,())
    assert not needs_fill_raster(page,('observed',))
    def forbidden():
        raise AssertionError('baseline must not acquire pixels')
    result,_=RecoveryAnalyzer().analyze(ir.document_id,page,Strategy(),base,forbidden)
    assert result is base


def test_text_prose_does_not_become_numeric_table(tmp_path):
    ir,page,base=fixture(tmp_path,ruled=False)
    blocks=tuple(b.model_copy(update={'spans':tuple(s.model_copy(update={'text_raw':'ordinary prose'})
        for s in b.spans)}) for b in page.blocks)
    page=page.model_copy(update={'blocks':blocks,'tables':()})
    result,_=RecoveryAnalyzer().analyze(ir.document_id,page,Strategy(text=True),
        base.model_copy(update={'tables':()}))
    assert not result.tables
