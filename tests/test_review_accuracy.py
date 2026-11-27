from doc_understanding.agents.roles import source_reviewer
from doc_understanding.runtime import budget as runtime_budget
from doc_understanding.runtime import model_client
from doc_understanding.runtime import settings
from doc_understanding.agents.workflows.evidence_review import graph
from doc_understanding.agents.workflows.evidence_review import service
from doc_understanding.tools import relation_index
from doc_understanding.validation import source_review
import copy
import json
import pytest
from tests.review_fixtures import source as source
from tests.review_fixtures import saved as saved


def sample():
    cell=lambda i:dict(cell_id=i,span_refs=[i],source_bbox=dict(x0=0,y0=0,x1=10,y1=10))
    doc=dict(physical_tables=[dict(table_id='P',page_number=1,cells=[cell('C')]),dict(table_id='T',page_number=1,cells=[cell('V')])],
        table_structure=[],document_ir={'pages':[]})
    nested={'cells':[dict(parent_cell_id='C',parent_native_table_id='N',page_number=1,child_table_ids=['T'],items=[])]}
    return doc,nested


def test_existing_link_is_presence_not_semantic_approval():
    doc,nested=sample();index=relation_index.RelationIndex(doc,nested)
    result=index.lookup('parent_table','C','T')
    assert result['status']=='present' and result['semantic_verified'] is False
    assert index.lookup('parent_table','V','P')['status']=='not_found_in_snapshot'
    assert index.lookup('parent_table','invented','T')['status']=='unknown'


def test_claimed_missing_is_rejected_but_incorrect_existing_is_reviewable():
    doc,nested=sample();index=relation_index.RelationIndex(doc,nested)
    a={'issues':[dict(issue_id='I1',claim='missing',relation='parent_table',source_id='C',target_id='T')]}
    assert relation_index.lookup_claims(a,index)[0]['contradiction']
    a['issues'][0]['claim']='incorrect'
    assert not relation_index.lookup_claims(a,index)[0]['contradiction']


def test_axis_lookup_uses_full_ancestor_path():
    doc,nested=sample()
    doc['table_structure']=[dict(table_id='P',row_tree=[dict(node_id='R',source_cell_id='C')],column_tree=[],cell_mappings=[dict(cell_id='V',row_path=['R'],column_path=[])])]
    assert relation_index.RelationIndex(doc,nested).lookup('row_header','C','V')['status']=='present'


def test_blind_review_excludes_feedback_and_extracted_relations():
    packet=dict(selection={'table_id':'T'},feedback={'note':'force error'},evidence=[dict(id='C',kind='cell',text='17')],
                extracted_candidates=['secret'],relation_inventory=['secret'],extracted_parent_links=['secret'],scope='page')
    blind=source_reviewer.blind_packet(packet)
    assert set(blind)=={'selection','evidence','scope'}
    assert 'secret' not in json.dumps(blind) and 'force error' not in json.dumps(blind)


def test_positive_storage_claim_is_also_checked():
    doc,nested=sample();index=relation_index.RelationIndex(doc,nested)
    a={'stored_claims':[dict(relation='row_header',source_id='C',target_id='V',stored_status='present')]}
    assert relation_index.check_stored_claims(a,index)[0]['contradiction']


def test_cell_membership_is_not_nested_parent_relation():
    source=dict(relations=[dict(source_id='C',target_id='T',relation='parent_table',citations=[dict(source_id='C',quote='17')])])
    packet=dict(evidence=[dict(id='C',text='17',kind='cell',table_id='T'),dict(id='T',text='',kind='table')])
    assert 'INVALID_NESTED_RELATION_ENDPOINTS' in source_review.check_source(packet,source)


def test_graph_one_extra_lookup_and_no_gold_approval(source):
    data,record=source;calls=[]
    class Mock:
        def call(self,role,packet,images,run_dir):
            calls.append((role,copy.deepcopy(packet)))
            if role=='source_review': out=dict(summary='원본 관찰',relations=[],missing_evidence=[])
            elif role in ('analyst_v2','reanalysis'):
                out=dict(summary='문제 확인 안 됨',issues=[],missing_evidence=[],requests=[dict(tool='read_source',source_id='C',target_id=None,relation=None,page=None,reason='원문 확인')] if role=='analyst_v2' else [])
            else: out=dict(summary='추가 오류 없음',assessments=[],additional_concerns=[],missing_evidence=[])
            return dict(status='completed',output=out,api_calls=1,usage={},estimated_cost_usd=0)
    result=graph.run_review(record,data,lambda _:None,'accuracy-test',calls=Mock())
    assert [r for r,_ in calls]==['source_review','analyst_v2','reanalysis','judge_v2']
    assert len(result['tool_results'])==1
    assert result['expansion_count']==1 and result['api_calls']==4
    assert not result['semantic_verified'] and not result['parser_modified']
    assert 'analyst_candidate' not in calls[0][1]
    assert calls[2][1]['tool_results'][0]['status']=='found'


def test_missing_source_blocks_every_call(source):
    data,record=source;(data/record['snapshot']['job_id']/'document.json').write_text('{}')
    class Never:
        def call(self,*args):raise AssertionError('must not call API')
    result=graph.run_review(record,data,lambda _:None,'blocked-test',calls=Never())
    assert result['api_calls']==0 and result['verdict']=='insufficient_evidence'


def test_insufficient_daily_budget_blocks_before_any_model_call(tmp_path, monkeypatch):
    import sqlite3
    from datetime import datetime, timezone, timedelta
    budget=tmp_path/'review-queue/agent-budget.sqlite3'
    budget.parent.mkdir()
    today=datetime.now(timezone(timedelta(hours=9))).date().isoformat()
    with sqlite3.connect(budget) as connection:
        connection.execute('CREATE TABLE calls (call_key TEXT PRIMARY KEY, day TEXT NOT NULL)')
        connection.execute('INSERT INTO calls VALUES (?,?)',('already-used',today))
    monkeypatch.setattr(settings,'configured',lambda _:dict(enabled=True,daily_limit=3))
    def never(*args,**kwargs):raise AssertionError('model adapter must not be created')
    monkeypatch.setattr(model_client,'ModelCalls',never)
    with pytest.raises(runtime_budget.DailyReviewBudgetExceeded):
        service.execute({},tmp_path,lambda _:None,'new-review')
