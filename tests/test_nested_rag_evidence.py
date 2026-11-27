"""Development-source integration checks; not independent generalization scores."""
from pathlib import Path
import pytest
from doc_understanding.domain.pipeline_contracts import CanonicalDocument
from doc_understanding.rag.index import make_entries, evidence_pack


@pytest.fixture(scope='module')
def hana():
    path=next(Path('data/semantic-panels/v1/development-batch/fc93d782e306e629').glob('*/document.json'))
    return CanonicalDocument.model_validate_json(path.read_bytes())


def test_nested_value_carries_parent_conditions_without_mutation(hana):
    before=hana.model_dump_json()
    entries=make_entries(hana)
    hit=next(e for e in entries if e['kind']=='table' and e['page']==4 and e['table_id'].endswith('physical005'))
    pack=evidence_pack(hana,[hit],'USD 현찰 수수료율')
    cash=next(e for e in pack if e.get('value_raw')=='1.50%')
    assert cash['nested_context']['coverage_status']=='complete'
    assert any('7일 이내' in x['text_raw'] for x in cash['nested_context']['prose'])
    assert cash['nested_context']['parent_cell_id']
    assert cash['semantic_status']=='unvalidated_candidate'
    assert hana.model_dump_json()==before


def test_narrative_hit_hydrates_child_table_and_keeps_offsets(hana):
    entries=make_entries(hana)
    hit=next(e for e in entries if e['kind']=='cell_text' and '7일 이내' in e['primary_text'])
    c=next(c for c in hana.contents if c.content_id==hit['content_id'])
    assert c.text_raw[hit['char_start']:hit['char_end']]==hit['primary_text']
    pack=evidence_pack(hana,[hit],'USD 현찰 수수료율과 출금 조건')
    assert any(e.get('value_raw')=='1.50%' for e in pack)
    assert any(e.get('value_kind')=='cell_text' and '7일 이내' in e['value_raw'] for e in pack)


def test_unresolved_mapping_cannot_become_narrative_value():
    path=Path('github-portfolio/table-aware-document-rag/examples/complex-results/canonical-document.json')
    doc=CanonicalDocument.model_validate_json(path.read_bytes())
    entries=make_entries(doc)
    hit=next(e for e in entries if e['kind']=='table' and e['page']==2 and e['table_id'].endswith('physical001'))
    pack=evidence_pack(doc,[hit],'Two people 2025')
    assert not any(e.get('table_id')==hit['table_id'] for e in pack)
    assert not any('Two people' in e.get('primary_text','') for e in entries if e['kind']=='cell_text')


def test_chunks_are_lossless_and_bounded():
    from doc_understanding.rag.indexing.nested import text_slices
    text='첫 번째 문장입니다.\n'+'긴 문장 '*300+'\n끝.'
    pieces=text_slices(text,100)
    assert ''.join(text[a:b] for a,b in pieces)==text
    assert all(0<b-a<=100 for a,b in pieces)


@pytest.mark.parametrize('field,value',[
    ('containment_status','unresolved'),('coverage_status','unresolved'),('reading_order_status','partial')])
def test_uncertain_parent_context_is_not_projected(hana,monkeypatch,field,value):
    from doc_understanding.rag.indexing import nested as nested_evidence
    links=nested_evidence.build_nested_content(hana)
    links=links.model_copy(update={'cells':tuple(c.model_copy(update={field:value}) for c in links.cells)})
    monkeypatch.setattr(nested_evidence,'build_nested_content',lambda doc:links)
    contexts,chunks,owned=nested_evidence.nested_projection(hana)
    assert owned and not contexts and not chunks


def test_current_word_does_not_block_a_historical_document_question(tmp_path,monkeypatch):
    from doc_understanding.rag import service
    calls=[]
    def generate(question,evidence,*args,**kwargs):
        calls.append(question)
        return {'delegated_to_model':True,'selection':{'evidence_ids':[]}}
    monkeypatch.setattr(service,'generate',generate)
    question='이 과거 문서에서 현재 적용된다고 설명한 수수료율은?'
    result=service.generate_answer(question,[{'evidence_id':'E1','value_raw':'1.50%'}],tmp_path)
    assert calls==[question] and result['delegated_to_model']


def test_resolver_fetches_a_run_once(hana,monkeypatch):
    from doc_understanding.rag import service
    calls=[]
    def fetch(rid):
        calls.append(rid)
        return hana
    monkeypatch.setattr(service.repository,'evidence_source',fetch)
    payload=next(e for e in make_entries(hana) if e['kind']=='cell_text')
    hit=dict(run_id='test-run',payload=payload)
    assert service.resolve_evidence([hit,hit],'수수료')
    assert calls==['test-run']


def test_sql_parent_text_mismatch_stops_before_model(hana,tmp_path,monkeypatch):
    from doc_understanding.rag import service
    hit=next(e for e in make_entries(hana) if e['kind']=='cell_text')
    pack=evidence_pack(hana,[hit],'수수료')
    for e in pack:e['run_id']='test-run'
    monkeypatch.setattr(service.repository,'sql_nested_probe',lambda *args:[
        {'contents':[],'parent_cell':{'span_refs':[]}}])
    monkeypatch.setattr(service,'generate',lambda *args,**kwargs:pytest.fail('MODEL_MUST_NOT_RUN'))
    with pytest.raises(ValueError,match='NESTED_SQL_PROSE_MISMATCH'):
        service.generate_answer('수수료',pack,tmp_path,verify_sql=True)


def test_nested_context_citation_is_rendered(hana):
    from doc_understanding.rag.generation.answer import render_answer
    hit=next(e for e in make_entries(hana) if e['kind']=='table' and e['page']==4 and e['table_id'].endswith('physical005'))
    pack=evidence_pack(hana,[hit],'USD 수수료율')
    e=next(e for e in pack if e.get('value_raw')=='1.50%')
    answer=render_answer(dict(evidence_ids=[e['evidence_id']],values_raw=[e['value_raw']],abstained=False),pack)
    assert '7일 이내' in answer and e['nested_context']['parent_cell_id'] in answer
