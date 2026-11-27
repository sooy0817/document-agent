import json
import os
import socket

import pytest

from tests.rag_assessment_helpers import receipt

from doc_understanding.rag.generation.answer import generate
from doc_understanding.parser.assembly.document import build_from_run
from doc_understanding.rag.index import evidence_pack, lexical_score, make_entries
from tests.test_document_rag import GANGDONG, ROOT


QUESTION='강동농협 재무현황에서 2025년 현금 금액은 얼마인가요?'


def fake_answer(evidence):
    value=next(e for e in evidence if e.get('value_raw')=='963'
               and e.get('column_path')==['2025년(B)','금액'] and e.get('row_path',[])[-1:] == ['1.현금'])
    def transport(payload,key,timeout):
        assert key=='test-key-not-a-real-openai-credential'
        assert payload['model']=='gpt-4.1' and payload['store'] is False
        data=json.loads(payload['input'][1]['content'])
        source=next(s for s in data['sources'] if s['source_id']==value['evidence_id'])
        selection=dict(evidence_ids=[value['evidence_id']],values_raw=['963'],abstained=False,
                       assessment=receipt(data['question'],[source]))
        return dict(status='completed',model='mock-gpt-4.1',usage=dict(input_tokens=100,output_tokens=20),
                    output=[dict(type='message',content=[dict(type='output_text',text=json.dumps(selection))])])
    return transport


def test_default_network_guard_blocks_real_api_before_dns_or_connect():
    assert os.environ['OPENAI_API_KEY']=='test-key-not-a-real-openai-credential'
    with pytest.raises(RuntimeError,match='NETWORK_BLOCKED'):
        socket.getaddrinfo('api.openai.com',443)
    with socket.socket() as connection:
        with pytest.raises(RuntimeError,match='NETWORK_BLOCKED'):
            connection.connect(('8.8.8.8',443))


def test_saved_document_search_cell_and_answer_with_mock(tmp_path):
    doc=build_from_run(GANGDONG)
    entries=make_entries(doc)
    hits=sorted(entries,key=lambda e:lexical_score(e['primary_text'],QUESTION),reverse=True)[:8]
    evidence=evidence_pack(doc,hits,QUESTION,max_cells=24)
    result=generate(QUESTION,evidence,tmp_path/'mock',allow_external=True,transport=fake_answer(evidence))
    assert '963' in result['answer'] and 'p.2' in result['answer'] and '미검증' in result['answer']
    assert result['model']=='mock-gpt-4.1'  # never label mock usage as a live call
    assert 'test-key-not-a-real-openai-credential' not in (tmp_path/'mock/request.json').read_text(encoding='utf8')


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get('NH_TEST_LOCAL_POSTGRES')!='1',reason='opt-in guarded local PostgreSQL; GPT always mocked')
def test_actual_postgres_document_to_mock_gpt_regression(tmp_path):
    from doc_understanding.rag.repository import fetch_document, search, sql_value_probe, store
    snapshot=ROOT/'data/document-rag/gangdong/document-v2.json'
    result=store(GANGDONG/'staging-manifest.json',snapshot)
    doc=fetch_document(result['run_id'])
    assert doc.model_dump(mode='json')==build_from_run(GANGDONG).model_dump(mode='json')
    hits=search(QUESTION,result['run_id'])
    evidence=evidence_pack(doc,[h['payload'] for h in hits],QUESTION,max_cells=24)
    cell=next(e for e in evidence if e.get('value_raw')=='963' and e.get('column_path')==['2025년(B)','금액'])
    sql=sql_value_probe(result['run_id'],cell['table_id'],cell['cell_id'])
    assert sql[0]['value_raw']=='963' and sql[0]['row_node_ids']==cell['row_node_ids']
    answer=generate(QUESTION,evidence,tmp_path/'pg-mock',allow_external=True,transport=fake_answer(evidence))
    assert answer['selection']['values_raw']==['963']
    assert answer['facts_published']==0 and '미검증' in answer['answer']
