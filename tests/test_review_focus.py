from tests.review_samples import packet, relation, outputs, empty_index
from doc_understanding.agents.workflows.axis_review import graph
from doc_understanding.tools import focused_evidence
from doc_understanding.tools import relation_index
from doc_understanding.validation import axes
import copy
import json

from tests.review_fixtures import source as source
from tests.review_fixtures import saved as saved






def test_axis_confusion_detected_but_row_and_column_not_blocked():
    p=packet()
    assert axes.relation_check(p,relation())['errors']==[]
    assert axes.relation_check(p,relation('column_header','C'))['errors']==[]
    assert 'AXIS_DIRECTION_CONFLICT' in axes.relation_check(p,relation('column_header','R'))['errors']
    assert 'AXIS_DIRECTION_CONFLICT' in axes.relation_check(p,relation('row_header','C'))['errors']


def test_fragment_header_preserved_as_candidate_not_rejected_or_approved():
    p=packet();p['evidence'][1]['table_id']='fragment'
    out=axes.relation_check(p,relation('column_header','C'))
    assert not out['errors'] and out['warnings']==['FRAGMENT_CONTINUITY_REQUIRES_REVIEW']


def test_blank_quote_and_missing_value_quote_are_not_evidence():
    r=relation();r['citations']=r['citations'][:1]
    assert 'BOTH_ENDPOINT_QUOTES_REQUIRED' in axes.relation_check(packet(),r)['errors']
    r['citations'][0]['quote']=''
    assert 'QUOTE_NOT_GROUNDED' in axes.relation_check(packet(),r)['errors']






def test_storage_absence_does_not_override_source_support():
    i,j=outputs();out=axes.validate(packet(),i,j,empty_index())
    assert out['verdict']=='source_supported_candidate'
    assert not out['semantic_verified'] and not out['training_ready']
    assert all(c['status']=='unknown' for c in out['storage_checks'])


def test_no_header_is_allowed_but_unknown_is_held():
    i,j=outputs();a=i['targets'][0]['row_axis']
    a.update(status='no_header',header_ids=[],reason='행 제목 없음')
    assert not axes.validate(packet(),i,j,empty_index())['errors']
    a.update(status='unknown')
    assert axes.validate(packet(),i,j,empty_index())['verdict']=='held'


def test_missing_target_judge_or_path_coverage_is_detected():
    i,j=outputs();j['decisions'].pop()
    assert 'JUDGE_AXIS_COVERAGE_MISMATCH' in axes.validate(packet(),i,j,empty_index())['errors']
    i['targets'][0]['column_axis']['header_ids']=[]
    assert 'HEADER_PATH_EMPTY' in axes.validate(packet(),i,j,empty_index())['errors']


def test_source_and_judge_do_not_receive_storage_or_user_claims(source,tmp_path):
    data,record=source
    captured=[]
    class Mock:
        def call(self,role,payload,images,run_dir):
            captured.append((role,copy.deepcopy(payload)))
            ids=payload['target_ids']
            axis=dict(status='unknown',header_ids=[],reason='근거 부족',citations=[])
            out=(dict(summary='미확정',targets=[dict(cell_id=s,role='unknown',row_axis=axis,column_axis=axis,context_ids=[]) for s in ids],missing_evidence=['원본 확인'])
                 if role=='interpret_axes' else dict(summary='보류',decisions=[dict(cell_id=s,axis=a,verdict='insufficient_evidence',reason='근거 부족',citations=[]) for s in ids for a in ('row','column')],missing_evidence=[]))
            return dict(status='completed',output=out,api_calls=0,usage={},estimated_cost_usd=0)
    out=graph.run_review(record,data,tmp_path/'run',calls=Mock())
    assert out['api_calls']==0 and out['result']['verdict']=='held'
    for _,payload in captured:
        assert not ({'feedback','relation_inventory','extracted_candidates','stored_claims','storage_checks'} & set(payload))
    assert [r for r,_ in captured]==['interpret_axes','judge_axes']
    assert out['parser_modified'] is False


def test_changed_source_blocks_preparation(source):
    import pytest
    data,record=source
    (data/record['snapshot']['job_id']/'document.json').write_text('{}')
    with pytest.raises(ValueError,match='SOURCE_CHANGED'):focused_evidence.prepare(record,data)
