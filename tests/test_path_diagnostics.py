"""Missing paths and collisions are diagnosed, never repaired by the audit."""
from doc_understanding.evaluation.path_diagnostics import missing_path, path_diagnostics
from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer
from tests.test_header_inheritance import table


def example():
    pt=table(1,30,True,1)
    st=SemanticTableAnalyzer().analyze(pt,header_rows=1,stub_columns=1)
    return pt,st,st.cell_mappings[0]


def view(st, blockers):
    nodes={n.node_id:n for n in st.row_tree+st.column_tree}
    return dict(table_codes=[],cells=[dict(id=m.cell_id,r=i,c=1,mapping_state=m.evidence_state,
        row_path=[nodes[n].label_raw for n in m.row_path],column_path=[nodes[n].label_raw for n in m.column_path],
        blockers=blockers.get(m.cell_id,[])) for i,m in enumerate(st.cell_mappings)])


def test_missing_axis_and_uncovered_geometry_are_separate():
    pt,st,m=example();c=next(c for c in pt.cells if c.cell_id==m.cell_id)
    assert missing_path('row',c,m,st,{'PANEL_SPLIT_AMBIGUOUS'})=='PANEL_SPLIT_AMBIGUOUS'
    empty=st.model_copy(update={'row_tree':()})
    assert missing_path('row',c,m,empty,set())=='NO_LABEL_NODES'
    absent=empty.model_copy(update={'panels':tuple(p.model_copy(update={'stub_columns':0}) for p in empty.panels)})
    assert missing_path('row',c,m,absent,set())=='AXIS_NOT_DECLARED'
    uncovered=st.model_copy(update={'row_tree':tuple(n.model_copy(update={'row_range':(50,51)}) for n in st.row_tree)})
    assert missing_path('row',c,m,uncovered,set())=='NO_COVERING_LABEL'


def test_value_crossing_multiple_headers_is_distinct_from_competing_labels():
    pt,st,m=example();c=next(c for c in pt.cells if c.cell_id==m.cell_id)
    wide=c.model_copy(update={'occupied_slots':((1,1),(1,2)),'colspan':2})
    assert missing_path('column',wide,m,st,set())=='VALUE_SPANS_MULTIPLE_HEADERS'
    node=st.row_tree[0]
    competing=st.model_copy(update={'row_tree':st.row_tree+(node.model_copy(update={'node_id':node.node_id+'/another'}),)})
    assert missing_path('row',c,m,competing,set())=='MULTIPLE_LABEL_CANDIDATES'


def test_same_text_in_different_rows_is_not_a_node_id_collision():
    pt,st,m=example();before=pt.model_dump_json(),st.model_dump_json()
    repeated=st.model_copy(update={'row_tree':tuple(n.model_copy(update={'label_raw':'Repeated'}) for n in st.row_tree)})
    v=view(repeated,{m.cell_id:['PATH_COLLISION']})
    result=path_diagnostics(pt,repeated,v)
    assert result['first_reason']['PATH_COLLISION']=={'SAME_TEXT_PATH_DIFFERENT_ROWS':1}
    assert before==(pt.model_dump_json(),st.model_dump_json())


def test_shared_node_path_collision_and_eligible_group_are_separate():
    pt,st,m=example();other=st.cell_mappings[2]
    dup=other.model_copy(update={'row_path':m.row_path,'column_path':m.column_path})
    collision=st.model_copy(update={'cell_mappings':tuple(dup if x.cell_id==other.cell_id else x for x in st.cell_mappings)})
    v=view(collision,{m.cell_id:['PATH_COLLISION'],other.cell_id:['LABEL_UNAVAILABLE','PATH_COLLISION']})
    result=path_diagnostics(pt,collision,v)
    assert result['first_reason']['PATH_COLLISION']=={'SAME_NODE_PATH_DIFFERENT_ROWS':1}
    assert result['all_blockers']['PATH_COLLISION']=={'SAME_NODE_PATH_DIFFERENT_ROWS':2}
    assert not path_diagnostics(pt,collision,view(collision,{}))['all_blockers']['PATH_COLLISION']
