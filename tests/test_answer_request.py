"""Immutable requested attribute: g-13 and a KB development-document name lookup."""
import copy
import json
from pathlib import Path

import pytest

from doc_understanding.rag.agentic.plan import QuestionPlan, validate_plan
from doc_understanding.rag.evidence.assessment import validate_assessment
from doc_understanding.rag.generation.answer import generate
from doc_understanding.rag.generation.narrative import build_catalog


def test_g13_recorded_procedure_cannot_broaden_name_requirement():
    x = json.loads((Path(__file__).parent/'fixtures/rag_p1/g13-wrong-procedure.json').read_text(encoding='utf8'))
    catalog = build_catalog(x['evidence'])
    assert validate_assessment(x['assessment'],x['question'],catalog,x['selection']['evidence_ids'])['passed']
    request = dict(question_quote='무엇이라고 하나요',requirement='직접 매수하는 방법의 명칭을 제시해야 한다.')
    checked = validate_assessment(x['assessment'],x['question'],catalog,x['selection']['evidence_ids'],answer_request=request)
    assert 'ANSWER_REQUEST_CHANGED_OR_OMITTED' in checked['reasons']
    assert not checked['passed']


@pytest.mark.parametrize('relax', [False,True])
def test_kb_development_name_request_is_preserved_through_generation(tmp_path, monkeypatch, relax):
    # KB development corpus p7: the named right, contrasted with its exercise procedure.
    question = 'KB 상품설명서에서 법 위반으로 계약 해지를 요구할 수 있는 권리는 무엇이라고 하나요?'
    request = dict(question_quote='권리는 무엇이라고 하나요',requirement='계약 해지를 요구할 수 있는 권리의 명칭',kind='name')
    value = '위법계약해지권'
    e = dict(evidence_id='E1',source_name='kb.pdf',page=7,table_id=None,
             value_raw=value,row_path=[],column_path=[])
    monkeypatch.setenv('OPENAI_API_KEY','test-key-never-sent')
    def transport(payload,*_):
        data=json.loads(payload['input'][1]['content'])
        assert data['answer_request']==request
        assert payload['text']['format']['schema']['properties']['evidence_ids']['items']['enum'] == ['E1']
        props = payload['text']['format']['schema']['$defs']['QuestionAssessment']['properties']
        assert props['requested_answer']['properties']['requirement']['enum'] == [request['requirement']]
        assert value in payload['text']['format']['schema']['properties']['values_raw']['items']['enum']
        source=data['sources'][0]
        requirement=dict(question_quote=request['question_quote'],requirement=request['requirement'],
                         status='supported',supports=[dict(source_id=source['source_id'],field='text_raw',quote=value)])
        if relax:
            requirement['requirement']+=' 또는 행사 절차'
        draft=dict(evidence_ids=['E1'],values_raw=[value],abstained=False,
                   assessment=dict(question=question,status='supported',requires_external_verification=False,
                                   requested_answer=requirement,requirements=[],explanation='명칭 근거'))
        return dict(status='completed',usage={},output=[dict(type='message',content=[dict(type='output_text',text=json.dumps(draft))])])
    result=generate(question,[e],tmp_path/'answer',allow_external=True,transport=transport,answer_request=request)
    assert result['selection']['abstained']==relax


def test_answer_request_must_quote_the_question():
    raw=dict(requirements=[dict(question_quote='명칭',role='item',text='명칭')],queries=['명칭'],
             answer_request=dict(question_quote='절차',requirement='절차를 설명',kind='procedure'))
    assert validate_plan(QuestionPlan.model_validate(raw),'명칭은?')['reasons']==['ANSWER_REQUEST_NOT_ANCHORED_IN_QUESTION']


def test_answer_request_strict_schema_requires_every_property():
    schema=QuestionPlan.model_json_schema()['$defs']['AnswerRequest']
    assert set(schema['required'])==set(schema['properties'])


def test_g13_adjacent_definition_is_preserved_as_a_separate_source():
    from doc_understanding.rag.evidence.adjacent import expand
    rows=json.loads((Path(__file__).parent/'fixtures/rag_p1/g13-nearby.json').read_text(encoding='utf8'))
    hit=next(c for c in rows if c['position']==62)
    original=dict(hit['body'],run_id='hana',evidence_id='E1')
    kept=copy.deepcopy(original)
    result=expand([original],lambda rid,ids: [c for c in rows if 60<=c['position']<=64])
    name=next(e for e in result if e['content_id']==next(c['content_id'] for c in rows if c['position']==64))
    assert '옵트인(OPT-IN)' in name['value_raw']
    assert name['source']==next(c['body']['source'] for c in rows if c['position']==64)
    assert result[0]==kept and original==kept
    assert len({e['evidence_id'] for e in result})==len(result)
