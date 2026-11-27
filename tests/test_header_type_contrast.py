"""Unstyled headers need repeated source-backed type contrast, not just a first row."""
from types import SimpleNamespace as NS

import pytest
from doc_understanding.domain.models import BlockIR, TextSpanIR
from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer
from tests.test_header_inheritance import table


def source(values=None):
    pt=table(1,30,True,1)
    # Two independent value columns, with a textual stub and numeric/date body.
    cells=[];blocks=[]
    for c in pt.cells:
        text=c.original_text
        if c.row_index>0:
            text=f'Group {c.row_index}' if c.column_index==0 else str(20250100+c.row_index) if c.column_index==1 else text
        if values and (c.row_index,c.column_index) in values:text=values[c.row_index,c.column_index]
        sid=c.cell_id+'/span';bid=c.cell_id+'/text'
        ref=c.source.model_copy(update={'cell_id':None,'span_id':sid,'block_id':bid})
        span=TextSpanIR(span_id=sid,source=ref,text_raw=text,line_index=0,font='Generic',font_size=10,flags=0)
        blocks.append(BlockIR(block_id=bid,kind='text',source=ref.model_copy(update={'span_id':None}),extraction_order=len(blocks),spans=(span,)))
        cells.append(c.model_copy(update={'original_text':text,'span_refs':(sid,)}))
    pt=pt.model_copy(update={'cells':tuple(cells)})
    page=NS(page_number=1,page_id='laboratory/p1',blocks=tuple(blocks))
    return pt,page


def test_repeated_numeric_and_date_columns_support_unstyled_header():
    pt,page=source();before=pt.model_dump_json()
    result=SemanticTableAnalyzer().analyze(pt,source_page=page)
    assert not any(a.code=='HEADER_REGION_AMBIGUOUS' for a in result.ambiguities)
    assert all(m.evidence_state=='inferred' for m in result.cell_mappings)
    assert any(i.code=='SOURCE_HEADER_TYPE_CONTRAST' for i in result.issues)
    assert pt.model_dump_json()==before


@pytest.mark.parametrize('damage',['numeric_first_row','one_numeric_column','missing_span','short_body','unresolved','blank_header','duplicate_header'])
def test_insufficient_or_conflicting_type_evidence_keeps_ambiguity(damage):
    changes={}
    if damage=='numeric_first_row':changes={(0,c):str(c+1) for c in range(3)}
    if damage=='one_numeric_column':changes={(r,1):'another sample' for r in range(1,5)}
    if damage=='blank_header':changes={(0,1):''}
    if damage=='duplicate_header':changes={(0,1):'Reading'}
    pt,page=source(changes)
    if damage=='missing_span':page.blocks=page.blocks[1:]
    if damage=='short_body':
        pt=pt.model_copy(update={'cells':tuple(c for c in pt.cells if c.row_index<3),'rows':pt.rows[:3]})
    if damage=='unresolved':pt=pt.model_copy(update={'cells':tuple(c.model_copy(update={'status':'unresolved'}) if c.row_index==1 else c for c in pt.cells)})
    result=SemanticTableAnalyzer().analyze(pt,source_page=page)
    assert any(a.code=='HEADER_REGION_AMBIGUOUS' for a in result.ambiguities)
    assert all(m.evidence_state=='unresolved' for m in result.cell_mappings)


@pytest.mark.parametrize("left,right,supported", [
    ("2024년(A)", "2025년(B)", True), ("FY2025", "FY2024", True),
    ("2024", "2025", False), ("2024년", "2027년", False),
    ("2024년 자료", "2025년 자료", False), ("FY2025", "FY2025", False),
])
def test_year_headers_require_explicit_periods_and_source_evidence(left, right, supported):
    pt, page = source({(0, 1):left, (0, 2):right, (2, 2):"△120"})
    def supported_header():
        result = SemanticTableAnalyzer().analyze(pt, source_page=page)
        return any("consecutive-years" in issue.message for issue in result.issues)
    assert supported_header() == supported
    page.blocks = page.blocks[1:]
    assert not supported_header()
