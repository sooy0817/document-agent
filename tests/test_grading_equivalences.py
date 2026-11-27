"""Versioned regrading of c-01/g-20, plus KB development monetary/location cases."""
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from doc_understanding.evaluation.rag.anchors import resolve_item
from doc_understanding.evaluation.rag.dataset import EvalItem,EvalSet,load
from doc_understanding.evaluation.rag.metrics import score_item,expected_match

ROOT=Path(__file__).resolve().parents[1]


def fixture(item_id):
    x=json.loads((ROOT/f'tests/fixtures/rag_p1/{item_id}-grading.json').read_text(encoding='utf8'))
    docs={did:NS(physical_tables=[NS(page_number=t['page_number'],cells=[NS(**c) for c in t['cells']]) for t in d['physical_tables']],
                 contents=[NS(**dict(c,source_references=[NS(**r) for r in c['source_references']])) for c in d['contents']])
          for did,d in x['source_excerpts'].items()}
    return x,docs


@pytest.mark.parametrize('item_id',['c-01','g-20'])
def test_recorded_answers_keep_v1_scores_and_pass_explicit_v2(item_id):
    x,docs=fixture(item_id)
    old,new=EvalItem.model_validate(x['item_v1']),EvalItem.model_validate(x['item_v2'])
    refs=resolve_item(new,x['documents'],docs)
    assert not score_item(old,x['original_resolved'],x['run'])['answer_correct']
    assert score_item(new,refs,x['run'])['answer_correct']
    assert new.question==old.question and new.expected==old.expected


@pytest.mark.parametrize('answer',['$2.5 billion','$25 trillion','$12.5 trillion','-$2.5 trillion','$2.5 trillionaire'])
def test_c01_rejects_different_amount_unit_or_number_substring(answer):
    x,docs=fixture('c-01');item=EvalItem.model_validate(x['item_v2'])
    x['run']['result']['answer_text']=answer
    x['run']['result']['selection']['values_raw']=[answer]
    assert not score_item(item,resolve_item(item,x['documents'],docs),x['run'])['answer_correct']


def test_c01_equivalent_text_still_requires_the_bis_document():
    x,docs=fixture('c-01');item=EvalItem.model_validate(x['item_v2'])
    for e in x['run']['evidence']:
        e['cell_id']='other-document/cell';e['content_id']='other-document/content'
    assert not score_item(item,resolve_item(item,x['documents'],docs),x['run'])['answer_correct']


def test_g20_equal_amount_in_another_row_is_not_an_equivalent_anchor():
    x,docs=fixture('g-20');item=EvalItem.model_validate(x['item_v2'])
    for e in x['run']['evidence']:
        if e['document_id']==x['documents']['shinhan']:
            e['cell_id']=e['document_id']+'/wrong-row';e['content_id']=None
    assert not score_item(item,resolve_item(item,x['documents'],docs),x['run'])['answer_correct']


def test_cross_document_equivalence_is_rejected_by_schema_check_and_resolver():
    x,docs=fixture('g-20');item=EvalItem.model_validate(x['item_v2'])
    item.gold[1].equivalent_locations[0].doc='kakao'
    dataset=EvalSet(name='bad',status='draft',reviewed_by_human=False,documents=x['documents'],items=[item])
    assert 'EQUIVALENT_LOCATION_DIFFERENT_DOCUMENT:g-20' in dataset.check()
    with pytest.raises(ValueError,match='EQUIVALENT_LOCATION_DIFFERENT_DOCUMENT'):
        resolve_item(item,x['documents'],docs)


def test_kb_development_amount_equivalence_is_explicit_and_number_bounded():
    # rag-dev-v1/kb-12: the source quotes 1억원. A reference expressed in won
    # describes that same amount; no currency conversion or runtime fact is created.
    assert expected_match('1억원까지 보호됩니다.','100,000,000원',['1억원'])
    assert not expected_match('11억원까지 보호됩니다.','100,000,000원',['1억원'])
    assert not expected_match('1만원까지 보호됩니다.','100,000,000원',['1억원'])
    assert not expected_match('1억원까지 보호됩니다.','100,000,000원',[])


def test_kb_development_equivalent_location_keeps_each_required_group():
    x,docs=fixture('dev-kb')
    did=x['documents']['kb'];doc=docs[did]
    data=load(ROOT/'config/evaluation/rag-dev-v1.json')
    original=next(i for i in data.items if i.id=='kb-12').model_copy(deep=True)
    # Reproduce a reference restricted to the complete table cell, while the
    # answer cites the original paragraph containing that same amount.
    original.gold[0].text=doc.physical_tables[0].cells[0].original_text
    original.require_selected_evidence=True
    revised=original.model_copy(deep=True)
    from doc_understanding.evaluation.rag.dataset import EvidenceLocation
    revised.gold[0].equivalent_locations=[EvidenceLocation(doc='kb',page=6,text='다른 보호상품과는 별도로 1인당')]
    refs=resolve_item(revised,x['documents'],docs)
    before=resolve_item(original,x['documents'],docs)
    assert len(refs['gold_groups'])==1 and len(refs['gold_groups'][0])==2
    assert not refs['unresolved_gold']
    assert before['gold']<refs['gold']
    run={'evidence':[{'evidence_id':'E1','document_id':did,'content_id':doc.contents[0].content_id}],
         'result':{'selection':{'abstained':False,'evidence_ids':['E1'],'values_raw':['1억원']},'answer_text':'1억원'}}
    assert not score_item(original,before,run,mode='answer')['answer_correct']
    assert score_item(revised,refs,run,mode='answer')['answer_correct']


@pytest.mark.parametrize('stem',['rag-general','rag-holdout'])
def test_version_change_preserves_every_question_and_original_expectation(stem):
    a=load(ROOT/f'config/evaluation/{stem}-v1.json');b=load(ROOT/f'config/evaluation/{stem}-v2.json')
    assert a.documents==b.documents and [i.id for i in a.items]==[i.id for i in b.items]
    for old,new in zip(a.items,b.items):
        assert old.question==new.question and old.expected==new.expected and old.answerable==new.answerable
        assert [g.model_dump(exclude={'equivalent_locations'}) for g in old.gold]==[g.model_dump(exclude={'equivalent_locations'}) for g in new.gold]
        if old.id not in ('c-01','g-20'):assert old==new
