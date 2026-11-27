import pytest
from tests.test_period_coverage import fixture,proposal,box,page,native
from tests.test_axis_resolver import cell,region
from types import SimpleNamespace as N
from doc_understanding.parser.context.period_expansion import expand_context, ExpansionAdapter, build_expansion_graph


def sources(r):
    parent=cell('parent-physical','original parent text',0,0).model_copy(update={
        'bbox':box(90,0,210,100),'table_id':'outer','spans':('v/span','prose/span')})
    return [r,region([parent])]


def test_expand_preserves_cells_and_covers_parent_source():
    r,d=fixture();before=r.model_dump_json();ss=sources(r)
    packet=expand_context(r,['v'],d,ss)
    assert packet['status']=='expanded'
    assert packet['after']['targets'][0]['status']=='checked_no_gap'
    assert packet['expanded_region']['cells'][0]==r.cells[0].model_dump(mode='json')
    assert packet['expanded_region']['cells'][1]==ss[1].cells[0].model_dump(mode='json')
    assert packet['additions'][0]['native_cell_id']=='parent'
    assert r.model_dump_json()==before


def test_no_usable_source_no_repair_and_budget_does_not_truncate():
    r,d=fixture();ss=sources(r)
    ss[1]=ss[1].model_copy(update={'cells':(ss[1].cells[0].model_copy(update={'physical_usable':False}),)})
    p=expand_context(r,['v'],d,ss)
    assert p['status']=='unchanged' and p['after']['targets'][0]['status']=='missing'
    p=expand_context(r,['v'],d,sources(r),max_cells=1)
    assert p['status']=='budget_blocked' and p['expanded_sha256']==p['original_sha256']
    assert p['attempted_additions'] and not p['additions']


def test_duplicate_identity_and_wrong_document_are_not_guessed():
    r,d=fixture();ss=sources(r)
    ss.append(region([ss[1].cells[0].model_copy(update={'id':'duplicate'})]))
    p=expand_context(r,['v'],d,ss)
    assert not p['additions'] and p['unresolved'][0]['reason']=='AMBIGUOUS_PHYSICAL_IDENTITY'
    with pytest.raises(ValueError,match='SOURCE_DOCUMENT_MISMATCH'):
        expand_context(r,['v'],d,[r.model_copy(update={'document_id':'other'})])


def test_adjacent_page_coordinates_keep_separate_page_identity():
    c=cell('v','value',0,0).model_copy(update={'bbox':box(10,930,200,980)})
    r=region([c]).model_copy(update={'bbox':c.bbox})
    nxt=cell('next','next text',0,0).model_copy(update={
        'table_id':'table-next','bbox':box(10,30,200,90),'spans':('next/span',)})
    other=region([nxt]).model_copy(update={'page':2,'bbox':nxt.bbox})
    doc=N(document_id='doc',pages=[
        page(1,[native('one',c.bbox,['v/span'])],box(10,600,200,980)),
        page(2,[native('two',nxt.bbox,['next/span'])],box(10,30,200,300))])
    packet=expand_context(r,['v'],doc,[r,other])
    from doc_understanding.agents.axis_review.axis_resolver import AxisRegion
    expanded=AxisRegion.model_validate(packet['expanded_region'])
    aliases,data=ExpansionAdapter(packet).request_builder(expanded,['v'])
    assert [x['page_number'] for x in data['source']['cells']]==[1,2]
    assert expanded.cells[1].bbox==nxt.bbox and not data['source']['shared_native_container']
    assert packet['after']['targets'][0]['status']=='checked_no_gap'


def test_graph_calls_only_one_fresh_pair_and_keeps_unknown():
    r,d=fixture();calls=[]
    class Mock:
        replay=True
        def call(self,region,targets,img,stage):
            calls.append(stage)
            p=proposal();p=p.model_copy(update={'decisions':[p.decisions[0].model_copy(update={
                'status':'insufficient_evidence','missing_evidence':'applicability remains ambiguous'})]})
            return p,dict(status='mock',model='mock',usage=None,estimated_cost_usd=0)
    result=build_expansion_graph(r,['v'],d,sources(r),lambda p:Mock(),lambda p:b'image').invoke({})
    assert calls==['propose','inventory'] and result['retry_count']==1
    assert result['result']['comparison']['targets'][0]['verdict']=='unjudgeable'
    assert result['result']['sidecar']['facts_published']==0
    calls.clear()
    result=build_expansion_graph(r,['v'],d,[r],lambda p:Mock(),lambda p:b'image').invoke({})
    assert calls==[] and result['retry_count']==0 and 'result' not in result


def test_expansion_does_not_change_prompts_or_reuse_saved_answers():
    from doc_understanding.parser.context.period_decisions import DecisionAdapter
    r,d=fixture();p=expand_context(r,['v'],d,sources(r))
    a=ExpansionAdapter(p)
    assert a.prompts==DecisionAdapter.prompts
    with pytest.raises(ValueError,match='EXPANSION_INPUT_MISMATCH'):
        a.request_builder(r,['v'])
