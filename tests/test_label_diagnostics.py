"""Blank source, split source and missing provenance must not be conflated."""
from types import SimpleNamespace as NS

import pytest
from doc_understanding.domain.models import CellIR, BlockIR, TextSpanIR
from doc_understanding.evaluation.label_diagnostics import blank_node, label_diagnostics
from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer
from tests.test_header_inheritance import table


def example(cause):
    pt=table(1,30,True,1)
    target=pt.cells[1].model_copy(update={'original_text':''})
    source=CellIR(cell_id='original',source=target.source,text_raw='',extraction_status='blank')
    blocks=()
    if cause=='split':
        box=target.source_bbox.model_copy(update={'x1':pt.cells[2].source_bbox.x1})
        source=source.model_copy(update={'source':source.source.model_copy(update={'bbox':box}),
                                         'text_raw':'Original heading','extraction_status':'observed','colspan':2})
    if cause=='unreadable':source=source.model_copy(update={'text_raw':None,'extraction_status':'unreadable'})
    if cause=='text_only':source=source.model_copy(update={'text_raw':'Linked text','extraction_status':'observed'})
    if cause=='span':
        span=TextSpanIR(span_id='raw',source=target.source,text_raw='Still here',line_index=0,font='X',font_size=10,flags=0)
        blocks=(BlockIR(block_id='b',kind='text',source=target.source,extraction_order=0,spans=(span,)),)
    target=target.model_copy(update={'source_cell_ids':('original',)})
    cells=list(pt.cells);cells[1]=target
    if cause=='split':cells[2]=cells[2].model_copy(update={'source_cell_ids':('original',)})
    pt=pt.model_copy(update={'cells':tuple(cells),'source_cells':() if cause=='unknown' else (source,), 'source_links':()})
    st=SemanticTableAnalyzer().analyze(pt,header_rows=1,stub_columns=1)
    node=next(n for n in st.column_tree if n.source_cell_id==target.cell_id)
    return pt,st,node,NS(blocks=blocks)


@pytest.mark.parametrize('case,cause',[
    ('blank','SOURCE_EXTRACTED_BLANK'),('split','MERGED_SOURCE_SPLIT_CANDIDATE'),
    ('unknown','SOURCE_PROVENANCE_INSUFFICIENT'),('unreadable','SOURCE_UNREADABLE'),('span','SOURCE_SPAN_PRESENT'),
    ('text_only','SOURCE_TEXT_PRESENT')])
def test_blank_label_cause_has_original_evidence_and_never_repairs(case,cause):
    pt,st,node,page=example(case);before=pt.model_dump_json(),st.model_dump_json()
    result=blank_node(node,pt,page)
    assert result['cause']==cause
    if case=='split':assert result['sibling_cell_ids']==[pt.cells[2].cell_id]
    assert before==(pt.model_dump_json(),st.model_dump_json())


def test_neighbor_text_without_shared_source_is_not_a_merge():
    pt,st,node,page=example('unknown')
    assert blank_node(node,pt,page)['cause']=='SOURCE_PROVENANCE_INSUFFICIENT'


def test_missing_original_page_cannot_prove_an_extracted_blank():
    pt,st,node,page=example('blank')
    assert blank_node(node,pt,None)['cause']=='SOURCE_PROVENANCE_INSUFFICIENT'


def test_cell_counts_and_distinct_label_counts_are_separate():
    pt,st,node,page=example('blank')
    maps=[m for m in st.cell_mappings if node.node_id in m.column_path]
    view={'cells':[dict(id=m.cell_id,r=i,c=1,mapping_state=m.evidence_state,
                       blockers=['LABEL_UNAVAILABLE'] if i else ['ROW_PATH_MISSING','LABEL_UNAVAILABLE']) for i,m in enumerate(maps)]}
    result=label_diagnostics(pt,st,page,view)
    assert result['all_blockers']=={'SOURCE_EXTRACTED_BLANK':4}
    assert result['first_reason']=={'SOURCE_EXTRACTED_BLANK':3}
    assert result['nodes_by_cause']=={'SOURCE_EXTRACTED_BLANK':1}
