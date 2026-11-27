import json
import pytest
from doc_understanding.agents.axis_review.axis_generation import RoleProposal, GroundedLink, GeneratedStructure, generation_request, bind_generated, validate_generated, AxisGenerationAdapter, build_generation_graph, generated_views
from doc_understanding.agents.axis_review.axis_resolver import rule_proposal
from tests.test_axis_resolver import matrix, region, cell


def from_rules(r):
    p=rule_proposal(r); roles={c.id:[] for c in r.cells}
    for link in p.links:
        roles[link.cell_id].append('value')
        for i in link.row_path: roles[i].append('row_label')
        for i in link.column_path: roles[i].append('column_header')
    return GeneratedStructure(roles=[RoleProposal(cell_id=i,roles=sorted(set(rs)) or ['corner'],
        evidence_cell_ids=[i],reason='source role') for i,rs in roles.items()],
        links=[GroundedLink(**link.model_dump(),evidence_cell_ids=[link.cell_id]+link.row_path+link.column_path) for link in p.links])


def merged():
    # Two-level columns with merged parent. No precomputed geometry options exist.
    r=region([cell('corner','항목',0,0),cell('year','기간',0,1),
              cell('h1','금액',1,1),cell('h2','비중',1,2),
              cell('label','예시',2,0),cell('v1','0',2,1),cell('v2','-2%',2,2)])
    r=r.model_copy(update={'cells':tuple(c.model_copy(update={'cs':2,
        'bbox':c.bbox.model_copy(update={'x1':300})}) if c.id=='year' else c for c in r.cells)})
    role={'corner':'corner','year':'column_header','h1':'column_header','h2':'column_header',
          'label':'row_label','v1':'value','v2':'value'}
    g=GeneratedStructure(roles=[RoleProposal(cell_id=i,roles=[rs],evidence_cell_ids=[i],reason='source') for i,rs in role.items()],
        links=[GroundedLink(cell_id=v,row_path=['label'],column_path=['year',h],
            row_axis='present',column_axis='present',reason='merged parent spans child columns',
            evidence_cell_ids=[v,'label','year',h]) for v,h in [('v1','h1'),('v2','h2')]])
    return r,g


def test_request_includes_source_spans_merges_but_no_option_list():
    r,g=merged(); aliases,data=generation_request(r)
    assert 'options' not in data
    assert any(c['colspan']==2 for c in data['cells'])
    assert all(c['span_refs'] for c in data['cells'])
    assert set(aliases.values())=={c.id for c in r.cells}


def test_generated_merged_paths_survive_as_review_candidates_not_verified():
    r,g=merged(); before=r.model_dump_json(); a=validate_generated(r,g)
    assert len(a['checked_links'])==2
    assert all(c['status']=='structurally_consistent_candidate' for c in a['checked_links'])
    assert all('HEADER_SUPPORT_NOT_INDEPENDENTLY_ESTABLISHED' in c['review_reasons'] for c in a['checked_links'])
    assert not a['semantic_verified'] and a['facts_published']==0
    assert r.model_dump_json()==before


def test_invented_id_wrong_roles_and_missing_cells_rejected():
    r,g=merged(); g=g.model_copy(update={'roles':g.roles[1:], 'links':[
        g.links[0].model_copy(update={'row_path':['fake']})]})
    a=validate_generated(r,g)
    assert 'UNKNOWN_CELL_ID' in a['checked_links'][0]['errors']
    assert 'ROLE_MISSING_OR_DUPLICATE' in a['cells'][0]['issues']
    assert any('VALUE_PATH_MISSING_OR_DUPLICATE' in c['issues'] for c in a['cells'])


def test_body_as_header_not_auto_certified_and_wrong_geometry_rejected():
    r,g=merged(); bad=g.links[0].model_copy(update={'column_path':['v2']})
    a=validate_generated(r,g.model_copy(update={'links':[bad,g.links[1]]}))
    assert 'AXIS_ROLE_CONFLICT' in a['checked_links'][0]['errors']
    assert 'COLUMN_GEOMETRY_CONFLICT' in a['checked_links'][0]['errors']


def test_numeric_row_identifier_remains_value_absent_axis_not_unknown():
    r=matrix(); g=from_rules(r)
    r=r.model_copy(update={'cells':tuple(c.model_copy(update={'text':'100'}) if c.id=='d' else c for c in r.cells)})
    rs=[x.model_copy(update={'roles':['value','row_label']}) if x.cell_id=='d' else
        x.model_copy(update={'roles':['column_header']}) if x.cell_id=='a' else x for x in g.roles]
    link=GroundedLink(cell_id='d',row_path=[],column_path=['a'],row_axis='absent',column_axis='present',
        evidence_cell_ids=['d','a'],reason='numeric identifier is original value')
    a=validate_generated(r,g.model_copy(update={'roles':rs,'links':g.links+[link]}))
    assert all(c['status']=='structurally_consistent_candidate' for c in a['checked_links'])
    assert 'AXIS_ABSENCE_NEEDS_SOURCE_REVIEW' in a['checked_links'][-1]['review_reasons']


def test_generation_graph_calls_model_without_geometry_options():
    pytest.importorskip('langgraph')
    from doc_understanding.agents.axis_review.axis_resolver import geometry_options
    r,g=merged(); assert not geometry_options(r)
    state=build_generation_graph(r,resolver=lambda _: (g,{'status':'mock'})).invoke({})
    assert state['model_invocations']==1
    assert len(state['generation_validation']['checked_links'])==2
    assert state['review_record']['review_status']=='pending'
    assert not state['review_record']['approved_for_training']


def test_generated_views_keep_raw_values_and_provenance():
    from tests.semantic_fixtures import compound
    from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer
    from doc_understanding.agents.axis_review.axis_resolver import CellEvidence
    t=compound()[0]; baseline=SemanticTableAnalyzer().analyze(t)
    r=region([CellEvidence(id=c.cell_id,table_id=t.table_id,text=c.original_text or '',bbox=c.source_bbox,
        spans=c.span_refs,physical_usable=True,row=c.row_index,col=c.column_index,rs=c.rowspan,cs=c.colspan,
        filled=False,bold=False) for c in t.cells])
    g=GeneratedStructure(roles=[RoleProposal(cell_id=c.id,roles=['unknown'],evidence_cell_ids=[c.id],reason='unclear') for c in r.cells],links=[])
    before=baseline.model_dump_json(); view=generated_views(r,validate_generated(r,g),[baseline.model_dump(mode='json')])[0]
    assert baseline.model_dump_json()==before
    assert {m['cell_id']:m['original_text'] for m in view['cell_mappings']}=={m.cell_id:m.original_text for m in baseline.cell_mappings}
    assert all(not m['row_path'] and not m['column_path'] for m in view['cell_mappings'])
    assert view['analysis_state']=='review_required'


def test_mock_live_adapter_binding_cost_and_no_gold_in_prompt():
    r=matrix(); g=from_rules(r); aliases,_=generation_request(r)
    reverse={v:k for k,v in aliases.items()}; alias_g=bind_generated(g,reverse)
    def transport(payload,key,timeout):
        assert key=='test-key-not-a-real-openai-credential'
        assert payload['store'] is False and payload['input'][1]['content'][1]['type']=='input_image'
        assert 'options' not in json.loads(payload['input'][1]['content'][0]['text'])
        return dict(status='completed',usage=dict(input_tokens=100,output_tokens=50),
            output=[dict(type='message',content=[dict(type='output_text',text=alias_g.model_dump_json())])])
    proposal,record=AxisGenerationAdapter(allow_external=True,transport=transport).resolve(r,b'image')
    assert proposal==g and record['estimated_cost_usd']==.000045
    with pytest.raises(ValueError,match='AUTHORIZED'): AxisGenerationAdapter().resolve(r)
    with pytest.raises(ValueError,match='BUDGET'): AxisGenerationAdapter(allow_external=True,max_calls=0).resolve(r)


def test_provider_failure_is_not_training_data_or_model_success():
    def fail(*_): raise OSError('secret-key-in-exception')
    g,record=AxisGenerationAdapter(allow_external=True,transport=fail).resolve(matrix())
    assert g is None and record['status']=='failed'
    assert 'secret-key' not in json.dumps(record)
