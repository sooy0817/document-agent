"""Stop repeated evidence; only a relevant new source permits a second answer."""
import copy
import json
from pathlib import Path

import pytest
from doc_understanding.rag.agentic import recovery, run
from doc_understanding.rag.agentic.plan import fallback_plan


def body(text='기존 근거',cid='old',**extra):
    return dict(evidence_id='E1',run_id='r',document_id='d',content_id=cid,value_raw=text,
                table_id=None,row_path=[],column_path=[],**extra)


def test_g10_recorded_external_ambiguity_has_no_identical_search_retry():
    x=json.loads((Path(__file__).parent/'fixtures/rag_p1/g10-retry-cause.json').read_text(encoding='utf8'))
    assert x['retried']
    decision=recovery.request(x['question'],fallback_plan(x['question']),x,[x['question']])
    assert not decision['queries'] and not decision['local_gap']
    assert decision['reason']=='EXTERNAL_VERIFICATION_REQUIRED'


def test_evidence_renumbering_and_losing_context_are_not_new_evidence():
    old=body(scope_context=[dict(content_id='heading',text_raw='제외 조건')])
    renumbered=dict(old,evidence_id='E99')
    assert recovery.atoms([old])==recovery.atoms([renumbered])
    assert not recovery.atoms([body()])-recovery.atoms([old])
    assert recovery.atoms([old])-recovery.atoms([body()])


@pytest.mark.parametrize('new,relevance,expected_calls,reason',[
    (False,3,1,'NO_NEW_RETRIEVED_EVIDENCE'),
    (True,0,1,'NEW_EVIDENCE_NOT_RELEVANT_TO_GAP'),
    (True,None,1,'RETRY_RELEVANCE_UNCONFIRMED'),
    (True,3,2,'NEW_EVIDENCE_FOR_GAP'),
])
def test_development_kb_gap_retries_only_for_new_relevant_source(tmp_path,monkeypatch,new,relevance,expected_calls,reason):
    # Same unsupported age question as rag-dev-v1/un-01. Retrieval/answer are mocked;
    # it does not invent an age answer for the actual KB document.
    question='KB퇴직연금정기예금의 가입 가능 연령 제한은?'
    initial=body('이 계약은 1개월에서 60개월까지 정할 수 있습니다.')
    fresh=body('추가로 찾은 연령 제한 관련 근거 후보',cid='new')
    gathered=[]
    def gather(q,plan,rid,retriever,**kwargs):
        gathered.append(kwargs)
        values=[initial,fresh] if len(gathered)>1 and new else [initial]
        return copy.deepcopy(values),dict(queries=list(kwargs.get('extra_queries')) if kwargs.get('retry_only') else [question],tables=[],hits=[])
    monkeypatch.setattr(run,'gather',gather)
    calls=[]
    def generate(q,evidence,output,**kwargs):
        calls.append(copy.deepcopy(evidence))
        return dict(answer='보류',model_calls=1,abstained=True,assessment_validation=dict(
            assessment=dict(requires_external_verification=False,requirements=[dict(
                question_quote='가입 가능 연령 제한',requirement='가입 가능 연령 제한',status='unsupported')]),
            reasons=['QUESTION_REQUIREMENTS_NOT_SUPPORTED']))
    class Ranker:
        def rerank(self,q,plan,evidence,limit,output):
            return evidence,dict(status='completed' if relevance is not None else 'failed',scores=[dict(evidence_id=e['evidence_id'],relevance=relevance if e['content_id']=='new' else 3) for e in evidence])
    report=run.answer(question,tmp_path/'run',generate=generate,reranker=Ranker())
    assert len(calls)==expected_calls
    assert len(gathered)==2 and gathered[1]['retry_only']
    assert gathered[1]['extra_queries']==['가입 가능 연령 제한']
    assert report['attempts'][1]['trace']['retry']['reason']==reason
    if expected_calls==1:
        assert report['evidence']==calls[0]


def test_recovery_cannot_reissue_an_already_tried_query_or_fix_contract_errors():
    question='가입 가능 연령 제한은?'
    result=dict(assessment_validation=dict(assessment=dict(requirements=[
        dict(question_quote='가입 가능 연령 제한',requirement='가입 가능 연령 제한',status='unsupported')])))
    r=recovery.request(question,fallback_plan(question),result,['가입가능연령제한'])
    assert not r['queries'] and r['reason']=='NO_NEW_QUERY'
    result['assessment_validation']['reasons']=['ANSWER_REQUEST_CHANGED_OR_OMITTED']
    assert not recovery.request(question,fallback_plan(question),result,[])['local_gap']


def test_targeted_gather_does_not_repeat_plan_or_original_queries(monkeypatch):
    seen=[]
    monkeypatch.setattr(run,'resolve_evidence',lambda *a,**k:[])
    def retrieve(q,*_): seen.append(q);return []
    run.gather('original',fallback_plan('original'),None,retrieve,extra_queries=['missing condition'],retry_only=True)
    assert seen==['missing condition']


def test_over_budget_expansion_does_not_reanswer_from_old_evidence(tmp_path,monkeypatch):
    e=body('기존 값',cell_id='c1');e['table_id']='t'
    question='필요한 조건은?'
    monkeypatch.setattr(run,'gather',lambda *a,**k:([e],dict(queries=[question],tables=[],hits=[])))
    extra=dict(e,cell_id='c2',value_raw='추가 조건')
    monkeypatch.setattr(run,'expand_units',lambda evidence:([*evidence,extra],dict(before=1,after=2,tables=[])))
    calls=[]
    def generate(q,evidence,output,**kwargs):
        calls.append(output.name)
        if len(calls)>1: raise ValueError('MODEL_REQUEST_BUDGET_EXCEEDED')
        return dict(answer='보류',model_calls=1,abstained=True,assessment_validation=dict(assessment=dict(
            requirements=[dict(question_quote=question,requirement=question,status='unsupported')],requires_external_verification=False)))
    report=run.answer(question,tmp_path/'run',generate=generate)
    assert calls==['answer','answer-whole']
    assert report['answer_model_calls']==1 and report['result']['answer']=='보류'
    assert report['evidence']==[e]
