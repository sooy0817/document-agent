import json
from pathlib import Path
from doc_understanding.parser.physical.physical_models import PhysicalTableIR
from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer, validate_hierarchy
from doc_understanding.parser.structure.repeated_panels import repeated_panel_ranges

def actual(case):
    p=Path('reports/semantic-tree-audit-20261028')/(case+'.json')
    return PhysicalTableIR.model_validate(json.loads(p.read_text(encoding='utf8'))['physical'])

def test_source_right_panel_and_original_preserved():
    p=actual('F01-p1-early');before=p.model_dump_json();s=SemanticTableAnalyzer().analyze(p)
    assert len(s.panels)==2
    nodes={n.node_id:n for n in s.row_tree+s.column_tree}
    for cell,label in [('cell0007','1개월 미만'),('cell0011','6개월 미만')]:
        m=next(m for m in s.cell_mappings if m.cell_id.endswith('/'+cell))
        assert nodes[m.row_path[-1]].label_raw.strip()==label
        assert m.evidence_state=='unresolved'
    assert len(s.cell_mappings)==6 # five nonblank rates plus one original blank
    assert p.model_dump_json()==before

def test_header_only_failure_is_diagnosed_not_repaired():
    s=SemanticTableAnalyzer().analyze(actual('P01-p14-rent'))
    assert not s.row_tree and not s.cell_mappings and s.panels[0].header_rows==4
    assert {'NO_BODY_MAPPING','HEADER_CONSUMES_ALL_ROWS'} <= {i.code for i in s.issues}

def test_rename_and_value_change_not_pdf_specific():
    p=actual('F01-p1-early')
    labels={(0,0):'Key',(0,1):'Result',(0,2):'Key',(0,3):'Result'}
    cells=tuple(c.model_copy(update={'original_text':labels.get((c.row_index,c.column_index),
                f'category-{c.row_index}-{c.column_index}' if c.column_index%2==0 else 'arbitrary prose')}) for c in p.cells)
    p=p.model_copy(update={'cells':cells,'table_id':'renamed','document_id':'unrelated'})
    assert repeated_panel_ranges(p)==((0,2),(2,4))
    assert len(SemanticTableAnalyzer().analyze(p).panels)==2

def test_repeated_headers_with_shared_rows_must_not_split():
    p=actual('F01-p1-early')
    left={c.row_index:c.original_text for c in p.cells if c.column_index==0}
    p=p.model_copy(update={'cells':tuple(c.model_copy(update={'original_text':left[c.row_index]}) if c.column_index==2 else c for c in p.cells)})
    assert not repeated_panel_ranges(p)

def test_numeric_stub_and_missing_label_abstain():
    p=actual('F01-p1-early')
    for label in ['123','']:
        q=p.model_copy(update={'cells':tuple(c.model_copy(update={'original_text':label}) if c.row_index==1 and c.column_index==2 else c for c in p.cells)})
        assert not repeated_panel_ranges(q)

def test_cross_panel_path_is_rejected_even_with_same_row():
    p=actual('F01-p1-early');s=SemanticTableAnalyzer().analyze(p)
    a=next(m for m in s.cell_mappings if m.cell_id.endswith('/cell0005'))
    b=next(m for m in s.cell_mappings if m.cell_id.endswith('/cell0007'))
    bad=s.model_copy(update={'cell_mappings':tuple(m.model_copy(update={'row_path':a.row_path}) if m==b else m for m in s.cell_mappings)})
    assert 'PANEL_CROSS_MAPPING' in {i.code for i in validate_hierarchy(p,bad)}

def test_independently_constructed_three_panels_text_values_and_zero():
    from tests.semantic_fixtures import compound
    from doc_understanding.domain.models import BBox,SourceRef
    from doc_understanding.parser.physical.physical_models import GridBand, PhysicalCell
    base=compound()[0];cells=[]
    for r in range(5):
        for c in range(9):
            b=BBox(x0=c*31,y0=r*17,x1=(c+1)*31,y1=(r+1)*17)
            label=['분류','설명','횟수'][c%3] if r==0 else (f'범주-{c//3}-{r}' if c%3==0 else '긴 설명문' if c%3==1 else '0')
            cid=f'synthetic-{r}-{c}'
            cells.append(PhysicalCell(cell_id=cid,row_index=r,column_index=c,rowspan=1,colspan=1,
                original_text=label,source_bbox=b,source=SourceRef(document_id='synthetic',page_number=1,bbox=b,cell_id=cid),
                extraction_method='vector_grid',status='resolved',occupied_slots=((r,c),)))
    p=base.model_copy(update={'cells':tuple(cells),'rows':tuple(GridBand(index=i,start=i*17,end=(i+1)*17) for i in range(5)),
        'columns':tuple(GridBand(index=i,start=i*31,end=(i+1)*31) for i in range(9)), 'bounding_box':BBox(x0=0,y0=0,x1=279,y1=85)})
    s=SemanticTableAnalyzer().analyze(p)
    assert [p.column_range for p in s.panels]==[(0,3),(3,6),(6,9)]
    assert len(s.cell_mappings)==24 and len(s.cell_roles)==45
    assert all(m.evidence_state=='unresolved' for m in s.cell_mappings)
    assert sum(m.original_text=='0' for m in s.cell_mappings)==12

def test_unknown_occupancy_retains_role_record():
    p=actual('F01-p1-early');c=p.cells[-1]
    p=p.model_copy(update={'cells':p.cells[:-1]+(c.model_copy(update={'occupied_slots':()}),)})
    s=SemanticTableAnalyzer().analyze(p)
    assert len(s.cell_roles)==len(p.cells)
    assert next(r for r in s.cell_roles if r.cell_id==c.cell_id).role=='unknown'

def test_reviewed_flat_layout_keeps_repeated_panels():
    # 구조 검토가 '머리글 1행·라벨 1열' 배치를 주어도 반복 머리글 패널은 그대로 나뉜다(오른쪽 값이 왼쪽 라벨에 붙지 않는다).
    from doc_understanding.parser.structure.layout import TableLayout
    p=actual('F01-p1-early');s=SemanticTableAnalyzer().analyze(p,layout=TableLayout(1,(1,)*len(p.rows)))
    assert len(s.panels)==2 and 'REPEATED_STUB_AS_VALUE' not in {i.code for i in s.issues}
    nodes={n.node_id:n for n in s.row_tree+s.column_tree}
    for cell,label in [('cell0007','1개월 미만'),('cell0011','6개월 미만')]:
        m=next(m for m in s.cell_mappings if m.cell_id.endswith('/'+cell))
        assert nodes[m.row_path[-1]].label_raw.strip()==label and m.evidence_state=='inferred'
    panels={p.panel_id:p.column_range for p in s.panels}
    assert all(panels[m.panel_id][0]<=next(c for c in p.cells if c.cell_id==m.cell_id).column_index<panels[m.panel_id][1]
               for m in s.cell_mappings)

def test_reviewed_headerless_layout_does_not_split_and_is_diagnosed():
    # 패널 전제(패널마다 머리글 1행)와 다른 배치는 나누지 않고, 라벨 열을 값으로 읽었다는 진단을 남긴다.
    from doc_understanding.parser.structure.layout import TableLayout
    p=actual('F01-p1-early');s=SemanticTableAnalyzer().analyze(p,layout=TableLayout(0,(1,)*len(p.rows)))
    assert len(s.panels)==1 and 'REPEATED_STUB_AS_VALUE' in {i.code for i in s.issues}
