import copy
import json
import pytest
from tests.rag_assessment_helpers import receipt

from doc_understanding.rag.generation.narrative import build_catalog, validate_render


@pytest.fixture
def example():
    # Synthetic entities exercise the same rule without a bank/document/currency constant.
    evidence=[dict(evidence_id='E1',document_id='D',source_name='synthetic.pdf',page=2,
        table_id='T',cell_id='C',value_raw='2.50%',row_path=['지폐','ABC'],column_path=['수수료율'],
        lookup_eligible=True,unit_context=[],notes=[],nested_context=dict(parent_cell_id='P',
            semantic_status='not_validated',reading_order_status='total',
            prose=[dict(content_id='B1',text_raw='- 시험통화(ABC)이외의 현찰을 입금하는 경우 ',span_refs=['s1']),
                   dict(content_id='B2',text_raw='- 시험통화 현찰 입금 후 9일 이내에 현찰이 아닌 수단으로 출금할 경우 ',span_refs=['s2']),
                   dict(content_id='B3',text_raw='- 현찰 이외의 수단으로 입금 후 현찰을 출금할 경우 ',span_refs=['s3'])]))]
    q='ABC 지폐 수수료율과 조건을 알려주세요.'
    catalog=build_catalog(evidence)
    parents=[s for s in catalog if s['kind']=='parent_text']
    sentences=[dict(text='문서에서 ABC 지폐 수수료율은 2.50%로 기재되어 있습니다.',kind='value',
        citations=[dict(source_id='E1',quote='2.50%')])]
    assessments=[]
    for s,scope in zip(parents,['excluded','direct','general_context']):
        text=s['text_raw'].strip().removeprefix('-').strip()
        sentence=dict(text=f'문서의 조건은 “{text}”로 기재되어 있습니다.',
            kind='exclusion' if scope=='excluded' else 'condition',citations=[dict(source_id=s['source_id'],quote=text)])
        assessments.append(dict(source_id=s['source_id'],applicability=scope,sentence=sentence))
    draft=dict(target_term='ABC',sentences=sentences,condition_assessments=assessments,abstained=False,reason='',
               assessment=receipt(q,catalog))
    return q,evidence,catalog,draft


def test_sentence_quotes_and_scope_are_linked(example):
    q,e,c,d=example;r=validate_render(d,c,q,e)
    assert r['sentence_validation']['passed']
    assert '2.50%' in r['answer'] and '[P2]' in r['answer']
    assert r['sentence_validation']['semantic_entailment_verified'] is False
    assert '[대상 제외 후보]' in r['answer'] and '[별도 문맥 조건]' in r['answer']


def test_applicability_is_a_model_candidate_not_a_lexical_verdict(example):
    q,e,c,d=copy.deepcopy(example)
    d['condition_assessments'][0]['applicability']='direct'
    d['condition_assessments'][0]['sentence']['kind']='condition'
    result=validate_render(d,c,q,e)
    # 원문 인용이 맞아도 적용 판단의 정답까지 인증하지 않는다.
    assert result['sentence_validation']['passed']
    assert result['sentence_validation']['semantic_entailment_verified'] is False


@pytest.mark.parametrize('mutation,reason',[
    ('number','UNSUPPORTED_NUMBER'),('citation','UNKNOWN_SOURCE'),('scope','UNRESOLVED_APPLICABILITY'),
    ('negation','CONDITION_QUOTE_NOT_PRESERVED'),('coverage','ASSESSED_SOURCE_NOT_CITED'),
    ('quote','QUOTE_NOT_IN_SOURCE'),('unresolved','UNRESOLVED_LOOKUP_BLOCKED')])
def test_unsafe_sentence_is_withheld(example,mutation,reason):
    q,e,c,d=copy.deepcopy(example)
    if mutation=='number':d['sentences'][0]['text']+=' 추가 8%입니다.'
    if mutation=='citation':d['sentences'][0]['citations'][0]['source_id']='invented'
    if mutation=='scope':
        d['condition_assessments'][0]['applicability']='uncertain'
        d['condition_assessments'][0]['sentence']['kind']='condition'
    if mutation=='negation':
        sentence=d['condition_assessments'][1]['sentence']
        sentence['text']=sentence['text'].replace('아닌','인')
    if mutation=='coverage':d['condition_assessments'].pop()
    if mutation=='quote':d['sentences'][0]['citations'][0]['quote']='3.50%'
    if mutation=='unresolved':c[0]['lookup_eligible']=False
    r=validate_render(d,c,q,e)
    assert r['selection']['abstained'] and reason in r['sentence_validation']['reasons']
    assert '2.50%' not in r['answer']


def test_model_reports_unresolved_generalization_and_answer_is_withheld(example):
    q,e,c,d=copy.deepcopy(example)
    d['assessment']['requirements'][0]['status']='ambiguous'
    result=validate_render(d,c,q,e)
    assert result['selection']['abstained']
    assert 'QUESTION_REQUIREMENTS_NOT_SUPPORTED' in result['sentence_validation']['reasons']


def test_numeric_answer_can_be_grounded_in_prose():
    q='실험은 며칠 동안 진행됐어?'
    e=[dict(evidence_id='E1',value_raw='실험은 17일 동안 진행됐다.',table_id=None,
            source_name='실험기록',page=1,row_path=[],column_path=[])]
    c=build_catalog(e)
    d=dict(target_term='',sentences=[dict(text='실험은 17일 동안 진행됐다.',kind='value',
        citations=[dict(source_id='E1',quote='실험은 17일 동안 진행됐다.')])],
        condition_assessments=[],abstained=False,reason='',assessment=receipt(q,c))
    result=validate_render(d,c,q,e)
    assert result['sentence_validation']['passed'] and not result['selection']['abstained']


def test_narrative_uses_shared_mock_transport(example,tmp_path):
    from doc_understanding.rag.generation.answer import generate
    q,e,c,d=example
    def fake(payload,key,timeout):
        assert key=='test-key-not-a-real-openai-credential'
        assert payload['text']['format']['name']=='grounded_sentence_answer'
        sent=json.loads(payload['input'][1]['content'])
        sources=sent['sources']
        # 출처는 문서 표시(doc)를 갖고, 문서 이름은 documents에 한 번만 실린다.
        assert sent['documents'][sources[0]['doc']]=='synthetic.pdf' and sources[0]['page']==2
        return dict(status='completed',usage=dict(input_tokens=10,output_tokens=20),output=[
            dict(type='message',content=[dict(type='output_text',text=json.dumps(d,ensure_ascii=False))])])
    result=generate(q,e,tmp_path/'generation',answer_mode='narrative',allow_external=True,transport=fake)
    assert result['sentence_validation']['passed']
    assert (tmp_path/'generation'/'source-catalog.json').exists()


def test_absent_period_model_assessment_blocks_narrative(example):
    q,e,c,d=copy.deepcopy(example)
    q='2026년 ABC 수수료율은?'
    d['assessment']=receipt(q,status='unsupported')
    result=validate_render(d,c,q,e)
    assert result['selection']['abstained']
    assert 'QUESTION_REQUIREMENTS_NOT_SUPPORTED' in result['sentence_validation']['reasons']
