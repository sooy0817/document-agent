from copy import deepcopy
from doc_understanding.agents.semantic_resolver.path_repair import repair_paths


def fixture():
    label = {'cell_id':'label','column_index':0,'row_index':1,'rowspan':1,'colspan':2,'original_text':'Arbitrary label','source_bbox':{},'status':'resolved','boundary_ids':[]}
    value = {**label,'cell_id':'value','column_index':2,'colspan':1,'original_text':'123'}
    p = {'cells':[label,value]}
    s = {'panels':[{'panel_id':'p','stub_columns':1,'header_rows':1}], 'row_tree':[], 'cell_mappings':[{'cell_id':'value','row_path':[],'column_path':['c'],'evidence_state':'unresolved'}]}
    return p,s


def test_merged_mapping_preserves_source_and_uncertainty():
    p,s=fixture()
    old=deepcopy((p,s))
    out,edges,trace=repair_paths(p,s,{})
    assert (p,s)==old
    assert out['cell_mappings'][0]['row_path']==['p/row/label']
    assert out['cell_mappings'][0]['evidence_state']=='unresolved'
    p['cells'][0]['original_text']='Totally different'
    assert repair_paths(p,s,{})[0]['cell_mappings']==out['cell_mappings']


def test_quarantine_is_not_root_confirmation():
    p,s=fixture()
    out,edges,trace=repair_paths(p,s,{'label':'outside'})
    assert 'label' not in edges
    assert trace[-1]['root_confirmed'] is False
    assert trace[-1]['previous_parent_cell_id']=='outside'


def test_multiple_panels_abstain():
    p,s=fixture()
    s['panels']*=2
    out,edges,trace=repair_paths(p,s,{'label':'outside'})
    assert out==s and edges=={'label':'outside'}
    assert trace[0]['action']=='ABSTAIN'
