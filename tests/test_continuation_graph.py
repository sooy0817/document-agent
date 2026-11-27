"""Network-free workflow contracts and source integrity, not PDF semantic Gold."""
import copy
from pathlib import Path

import pytest

from doc_understanding.domain.pipeline_contracts import CanonicalDocument
from doc_understanding.parser.relations.continuation_graph import build_continuation_graph, discover_pairs, collect_packet, validate_decision
from doc_understanding.parser.relations.continuation_provider import ContinuationModel, bind_ids


@pytest.fixture(scope='module')
def document():
    path = Path('work/document-explorer/85373bab3d984c23beb3abe9663b27d1/document.json')
    if not path.exists():
        pytest.skip('private frozen source not bundled')
    return CanonicalDocument.model_validate_json(path.read_bytes())


def pair_for(document, a=10, b=11):
    return next(p for p in discover_pairs(document) if (p['source_page'],p['target_page'])==(a,b))


def decision(packet, status='link_candidate'):
    refs = []
    for tid in (packet['pair']['source_table_id'],packet['pair']['target_table_id']):
        c = next(c for c in packet['registry'].values() if c.get('table_id')==tid and c['text'].strip())
        refs.append(dict(source_id=c['id'], quote=c['text'], reason='source quotation'))
    return dict(decision=status, evidence_sufficient=status!='insufficient_evidence',
        supporting_evidence=refs, contradicting_evidence=[],
        missing_evidence=['earlier heading'] if status=='insufficient_evidence' else [],
        scopes=[], reason='Test mock, not a semantic conclusion')


class MockModel:
    def __init__(self, decisions=None, review='supported'):
        self.decisions = list(decisions or ['link_candidate'])
        self.review = review
        self.calls = 0

    def judge(self, packet, images, proposal):
        self.calls += 1
        record = dict(status='mock', attempts=1, estimated_cost_usd=0)
        if proposal is None:
            return decision(packet, self.decisions.pop(0)), record
        return dict(verdict=self.review, scope_complete=True,
                    evidence=proposal['supporting_evidence'], reason='source checked mock'), record


def invoke(document, model):
    return build_continuation_graph(document, pair_for(document), model=model,
                                   image_loader=lambda _: {10:b'mock',11:b'mock'}).invoke({})


def test_reducers_and_raw_ir_preserved(document):
    before = document.model_dump_json()
    state = invoke(document, MockModel())
    assert [s['node'] for s in state['steps']]==['collect_evidence','assess_coverage',
        'judge_relation','validate_relation','review_relation','finalize']
    assert len(state['model_records'])==state['model_calls']==2
    assert state['final']['decision']=='link_candidate'
    assert state['final']['semantic_verified'] is False
    assert document.model_dump_json()==before


def test_missing_evidence_expands_once_then_abstains(document):
    model=MockModel(['insufficient_evidence','insufficient_evidence'])
    state=invoke(document, model)
    assert model.calls==2
    assert state['expansion_count']==1
    assert sum(s['node']=='expand_evidence' for s in state['steps'])==1
    assert state['final']['decision']=='insufficient_evidence'


def test_reviewer_rejection_holds_instead_of_publishing(document):
    state=invoke(document, MockModel(review='rejected'))
    assert state['final']['decision']=='insufficient_evidence'
    assert state['proposal']['decision']=='link_candidate'
    assert state['final']['scopes']==[]


def test_candidates_include_rule_misses_and_negative_tables(document):
    pairs=discover_pairs(document)
    assert any((p['source_page'],p['target_page'])==(19,20) for p in pairs)
    assert any((p['source_page'],p['target_page'])==(11,12) for p in pairs)


@pytest.mark.parametrize('fault,code',[
    ('quote','QUOTE_NOT_IN_SOURCE'),('id','UNKNOWN_EVIDENCE_ID'),
    ('missing','INSUFFICIENT_SOURCE_CANNOT_BE_DECIDED'),
    ('scope','INVALID_TARGET_CELL'),('uncited','SCOPE_SOURCE_NOT_CITED')])
def test_false_evidence_or_scope_is_rejected(document,fault,code):
    packet=collect_packet(document,pair_for(document))
    d=decision(packet)
    if fault=='quote': d['supporting_evidence'][0]['quote']='this is not printed anywhere'
    elif fault=='id': d['supporting_evidence'][0]['source_id']='invented'
    elif fault=='missing': d['missing_evidence']=['previous page missing']
    else:
        a,b=d['supporting_evidence']
        d['scopes']=[dict(kind='header_inheritance',source_cell_ids=[a['source_id']],
            target_cell_ids=['unknown'] if fault=='scope' else [b['source_id']],
            evidence=[a] if fault=='scope' else [b], reason='test')]
    assert code in validate_decision(packet,d)['errors']


def test_provider_never_calls_without_opt_in():
    with pytest.raises(ValueError,match='EXTERNAL_CALL_NOT_AUTHORIZED'):
        ContinuationModel().judge({}, {}, None)


def test_alias_binding_preserves_strings_and_unknown_ids():
    result=bind_ids(dict(source_id='R0',quote='R0',target_cell_ids=['bad']), {'R0':'actual'})
    assert result==dict(source_id='actual',quote='R0',target_cell_ids=['INVALID_ID:bad'])


def test_model_failure_is_bounded_and_sanitized(document):
    class Failed:
        def judge(self,*args): raise RuntimeError('sk-never-log-this')
    state=invoke(document, Failed())
    assert state['final']['decision']=='insufficient_evidence'
    assert state['expansion_count']==0
    assert 'sk-never' not in str(state)


def test_incomplete_physical_source_never_calls_model(document):
    pair=pair_for(document)
    target=next(t for t in document.physical_tables if t.table_id==pair['target_table_id'])
    cell=target.cells[0].model_copy(update={'status':'unresolved'})
    target=target.model_copy(update={'cells':(cell,)+target.cells[1:]})
    changed=document.model_copy(update={'physical_tables':tuple(target if t.table_id==target.table_id else t for t in document.physical_tables)})
    model=MockModel()
    state=invoke(changed,model)
    assert model.calls==0 and state['final']['decision']=='insufficient_evidence'


def test_missing_context_cannot_mean_separate(document):
    packet=collect_packet(document,pair_for(document))
    d=decision(packet,'separate');d['evidence_sufficient']=False
    assert not validate_decision(packet,d)['valid']


def test_sibling_headers_cannot_be_applied_as_one_path(document):
    packet=collect_packet(document,pair_for(document,12,12))
    d=decision(packet)
    cells=[c for c in packet['registry'].values() if c.get('table_id')==packet['pair']['source_table_id']]
    target=next(c for c in packet['registry'].values() if c.get('table_id')==packet['pair']['target_table_id'])
    def scope(sources):
        return dict(kind='header_inheritance',source_cell_ids=[c['id'] for c in sources],
            target_cell_ids=[target['id']], evidence=[dict(source_id=c['id'],quote=c['text'],reason='source') for c in sources],reason='test')
    d['scopes']=[scope(cells[:2])]
    assert 'SCOPE_MUST_HAVE_ONE_SOURCE_HEADER' in validate_decision(packet,d)['errors']
    d['scopes']=[scope([cells[0]]),scope([cells[1]])]
    assert 'SIBLING_HEADERS_FOR_SAME_TARGET' in validate_decision(packet,d)['errors']


def test_actual_blank_is_geometry_evidence_not_invented_quote(document):
    from doc_understanding.parser.relations.continuation_graph import evidence_errors, SourceEvidence
    packet=collect_packet(document,pair_for(document))
    cell=next(c for c in packet['registry'].values() if c['kind']=='cell' and not c['text'].strip())
    e=SourceEvidence(source_id=cell['id'],quote='',reason='blank continuation slot')
    assert not evidence_errors([e],packet['registry'])
    nonblank=next(c for c in packet['registry'].values() if c['text'].strip())
    assert evidence_errors([e.model_copy(update={'source_id':nonblank['id']})],packet['registry'])


def test_new_body_topics_do_not_prove_separate_table(document):
    packet=collect_packet(document,pair_for(document,23,24))
    d=decision(packet,'separate')
    assert 'SEPARATION_BOUNDARY_NOT_CITED' in validate_decision(packet,d)['errors']
    assert not packet['separation_boundary_ids']
