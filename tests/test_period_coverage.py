from types import SimpleNamespace as N
from doc_understanding.domain.models import BBox
from tests.test_axis_resolver import cell,region
from doc_understanding.parser.context.period_decisions import PeriodDecision, PeriodDecisions
from doc_understanding.parser.context.period_coverage import audit_context, guard_decisions, build_coverage_graph

def box(x0,y0,x1,y1):return BBox(x0=x0,y0=y0,x1=x1,y1=y1)
def native(id,b,spans):return N(cell_id=id,source=N(bbox=b),span_refs=tuple(spans))
def page(n,cells,bounds=None):
    tables=[N(table_candidate_id='native'+str(n),cells=cells,source=N(bbox=bounds or box(0,0,200,200)))] if cells else []
    spans=[N(span_id=s,text_raw='source text',source=N(bbox=c.source.bbox)) for c in cells for s in c.span_refs]
    return N(page_number=n,width=300,height=1000,tables=tables,blocks=[N(spans=spans)],text_status='native')
def fixture():
    c=cell('v','value',1,1);r=region([c]);p=page(1,[native('parent',box(90,0,210,100),['v/span','prose/span'])])
    doc=N(document_id='doc',pages=[p]);return r,doc
def proposal():
    return PeriodDecisions(decisions=[PeriodDecision(target_cell_id='v',status='not_needed',relations=[],supporting_cell_ids=[],reason='no printed definition in supplied crop',missing_evidence=None)])

def test_missing_parent_blocks_absence_without_mutating_response():
    r,d=fixture();p=proposal();before=p.model_dump_json()
    audit=audit_context(r,['v'],d)
    assert audit.targets[0].status=='missing'
    assert 'prose/span' in audit.targets[0].missing_span_ids
    effective,changes=guard_decisions(r,p,audit)
    assert effective.decisions[0].status=='insufficient_evidence'
    assert p.model_dump_json()==before and len(changes)==1

def test_present_parent_refs_and_source_registry_mismatch():
    r,d=fixture();extra=cell('prose','source text',0,0).model_copy(update={'spans':('prose/span',)})
    r=r.model_copy(update={'cells':r.cells+(extra,)})
    audit=audit_context(r,['v'],d)
    assert audit.targets[0].status=='checked_no_gap'
    assert guard_decisions(r,proposal(),audit)[0].decisions[0].status=='not_needed'
    d.document_id='wrong'
    assert audit_context(r,['v'],d).targets[0].status=='unknown'

def test_unknown_inventory_and_stale_audit_fail_closed():
    r,d=fixture();audit=audit_context(r,['v'],None)
    assert guard_decisions(r,proposal(),audit)[0].decisions[0].status=='insufficient_evidence'
    stale=r.model_copy(update={'cells':(r.cells[0].model_copy(update={'text':'different'}),)})
    effective,changes=guard_decisions(stale,proposal(),audit)
    assert effective.decisions[0].status=='insufficient_evidence'
    assert 'AUDIT_INPUT_MISMATCH' in changes[0]['reasons']

def test_next_page_alignment_is_risk_not_automatic_relation():
    c=cell('v','value',0,0).model_copy(update={'bbox':box(10,930,200,980)})
    r=region([c]).model_copy(update={'bbox':c.bbox})
    first=page(1,[native('one',c.bbox,['v/span'])],box(10,600,200,980))
    second=page(2,[native('two',box(10,30,200,90),['next/span'])],box(10,30,200,300))
    audit=audit_context(r,['v'],N(document_id='doc',pages=[first,second]))
    assert audit.targets[0].status=='missing'
    assert any(f.kind=='next_page' and f.status=='missing' for f in audit.targets[0].findings)
    effective,_=guard_decisions(r,proposal(),audit)
    assert effective.decisions[0].relations==[]
    second.tables[0].source.bbox=box(220,30,290,300)
    assert audit_context(r,['v'],N(document_id='doc',pages=[first,second])).targets[0].status=='checked_no_gap'

def test_previous_page_missing_and_document_boundary():
    c=cell('v','value',0,0).model_copy(update={'bbox':box(10,30,200,80)})
    r=region([c]).model_copy(update={'page':2,'bbox':c.bbox})
    first=page(1,[native('one',box(10,930,200,980),['prev/span'])],box(10,600,200,980))
    second=page(2,[native('two',c.bbox,['v/span'])],box(10,30,200,300))
    audit=audit_context(r,['v'],N(document_id='doc',pages=[first,second]))
    assert any(f.kind=='previous_page' and f.status=='missing' for f in audit.targets[0].findings)
    r=r.model_copy(update={'page':1});second.page_number=1
    assert audit_context(r,['v'],N(document_id='doc',pages=[second])).targets[0].status=='checked_no_gap'


def test_text_tight_native_box_matches_by_exact_spans_not_tolerance():
    c=cell('v','value',5,1);r=region([c])
    tight=box(102,155,198,170)
    d=N(document_id='doc',pages=[page(1,[native('native-v',tight,['v/span'])])])
    assert audit_context(r,['v'],d).targets[0].status=='checked_no_gap'
    d.pages[0].tables[0].cells[0].span_refs=('different/span',)
    assert audit_context(r,['v'],d).targets[0].status=='unknown'


def test_second_row_near_page_top_is_not_continuation_edge():
    c=cell('v','value',0,0).model_copy(update={'bbox':box(10,80,200,110)})
    r=region([c]).model_copy(update={'page':2,'bbox':c.bbox})
    prev=page(1,[native('prev',box(10,930,200,980),['prev/span'])],box(10,600,200,980))
    current=page(2,[native('preceding',box(10,30,200,80),['other/span']),native('current',c.bbox,['v/span'])],box(10,30,200,300))
    audit=audit_context(r,['v'],N(document_id='doc',pages=[prev,current]))
    assert next(f for f in audit.targets[0].findings if f.kind=='previous_page').status=='not_applicable'


def test_graph_preserves_raw_outputs_and_never_scores_unknown_as_correct():
    r,d=fixture();p=proposal()
    class Replay:
        replay=True
        def call(self,*args):return p,dict(status='replayed',model='mock',usage=None,estimated_cost_usd=0)
    result=build_coverage_graph(r,['v'],b'fake',Replay(),d).invoke({})
    assert result['raw']['proposal']==p.model_dump(mode='json')
    assert result['guarded_proposal']['decisions'][0]['status']=='insufficient_evidence'
    assert result['comparison']['targets'][0]['verdict']=='unjudgeable'
    assert result['sidecar']['status']=='review_required' and result['sidecar']['facts_published']==0


def test_guard_does_not_hide_original_schema_conflict():
    from doc_understanding.parser.context.period_decisions import DefinitionApplication
    r,d=fixture();p=proposal()
    p=p.model_copy(update={'decisions':[p.decisions[0].model_copy(update={'relations':[DefinitionApplication(definition_cell_ids=['v'],duration_label_cell_id='v')]})]})
    class Replay:
        replay=True
        def call(self,*args):return p,dict(status='replayed',model='mock',usage=None,estimated_cost_usd=0)
    result=build_coverage_graph(r,['v'],b'fake',Replay(),d).invoke({})
    assert not result['comparison']['valid']
    assert result['sidecar']['status']=='rejected'
    assert 'ROLE_CONFLICT' in result['sidecar']['validation_errors']
