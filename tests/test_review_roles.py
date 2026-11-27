from doc_understanding.agents.workflows.table_review import graph
from doc_understanding.tools import relation_candidates
from doc_understanding.validation import relations
import copy

from tests.review_samples import packet,outputs,empty_index
from tests.review_fixtures import source as source
from tests.review_fixtures import saved as saved


def candidates():return relation_candidates.with_candidates(packet())


def coverage(requirement='required'):
    p=candidates();texts={e['id']:e['text'] for e in p['evidence']}
    return [dict(**c,requirement=requirement,reason='원본 범위 검토',citations=[
        dict(source_id=c['header_id'],quote=texts[c['header_id']]),dict(source_id=c['cell_id'],quote=texts[c['cell_id']])]) for c in p['geometric_candidates']]


def test_description_role_does_not_suppress_missing_item():
    i,j=outputs();i['targets'][0]['role']='description'
    i['targets'][0]['row_axis'].update(status='no_header',header_ids=[])
    j['coverage']=coverage()
    result=relations.audit_coverage(candidates(),i,j)
    assert 'REQUIRED_HEADER_MISSING' in result['errors']
    assert result['missing_relations'][0]['header_id']=='R'


def test_geometry_alone_does_not_force_relation():
    i,j=outputs();i['targets'][0]['row_axis'].update(status='no_header',header_ids=[])
    j['coverage']=coverage()
    next(c for c in j['coverage'] if c['axis']=='row')['requirement']='not_required'
    assert not relations.audit_coverage(candidates(),i,j)['errors']


def test_judge_cannot_silently_skip_candidate_or_hide_uncertainty():
    i,j=outputs();j['coverage']=coverage('insufficient_evidence')
    assert relations.audit_coverage(candidates(),i,j)['warnings']==['CANDIDATE_SCOPE_UNCERTAIN']
    j['coverage'].pop()
    assert 'CANDIDATE_COVERAGE_MISMATCH' in relations.audit_coverage(candidates(),i,j)['errors']


def test_target_quote_is_checked_and_description_can_have_axes():
    i,j=outputs();t=i['targets'][0]
    role=dict(summary='역할',targets=[dict(cell_id='V',role='description',reason='줄글',citations=[dict(source_id='V',quote='조건 없음')])],missing_evidence=[])
    relation=dict(summary='관계',targets=[{k:v for k,v in t.items() if k!='role'}|dict(target_quote='조건 없음')],missing_evidence=[])
    j['coverage']=coverage()
    merged,result=relations.validate(candidates(),role,relation,j,empty_index())
    assert merged['targets'][0]['role']=='description' and not result['errors']
    assert result['semantic_verified'] is False
    relation['targets'][0]['target_quote']='원문에 없음'
    assert 'TARGET_QUOTE_NOT_GROUNDED' in relations.validate(candidates(),role,relation,j,empty_index())[1]['errors']


def test_role_output_never_enters_relation_or_judge_input(source,tmp_path):
    data,record=source;payloads=[]
    class Mock:
        def call(self,name,p,images,run_dir):
            payloads.append((name,copy.deepcopy(p)));ids=p['target_ids']
            sources={e['id']:e['text'] for e in p['evidence']}
            if name=='classify_roles':
                out=dict(summary='ROLE_ONLY_MARKER',targets=[dict(cell_id=s,role='description',reason='ROLE_ONLY_MARKER',citations=[dict(source_id=s,quote=sources[s])]) for s in ids],missing_evidence=[])
            elif name=='infer_relations':
                axis=dict(status='unknown',header_ids=[],reason='불확실',citations=[])
                out=dict(summary='관계',targets=[dict(cell_id=s,target_quote=sources[s],row_axis=axis,column_axis=axis,context_ids=[]) for s in ids],missing_evidence=[])
            else:
                out=dict(summary='보류',decisions=[dict(cell_id=s,axis=a,verdict='insufficient_evidence',reason='불확실',citations=[]) for s in ids for a in ['row','column']],coverage=[],missing_evidence=[])
            return dict(status='completed',output=out,api_calls=0,estimated_cost_usd=0)
    result=graph.run_review(record,data,tmp_path/'roles-run',calls=Mock())
    import json
    assert 'ROLE_ONLY_MARKER' not in json.dumps(payloads[1:])
    assert result['api_calls']==0 and result['result']['verdict']=='held'
