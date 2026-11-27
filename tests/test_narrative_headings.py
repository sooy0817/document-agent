"""g-10 and Suhyup development corpus: preserve real section titles and provenance."""
import copy
import json
from pathlib import Path

import pytest
from doc_understanding.rag.evidence.headings import attach, contexts
from doc_understanding.rag.generation.narrative import build_catalog
from doc_understanding.rag.generation.answer import template_text


def fixture(name,needle):
    raw=json.loads((Path(__file__).parent/f'fixtures/rag_p1/{name}-headings.json').read_text(encoding='utf8'))
    c=next(c for c in raw['contents'] if needle in c['text_raw'])
    e=dict(evidence_id='E1',run_id=name,document_id=c['source_references'][0]['document_id'],
           content_id=c['content_id'],value_raw=c['text_raw'],page=c['source_references'][0]['page_number'],
           row_path=[],column_path=[],table_id=None)
    return e,raw


@pytest.mark.parametrize('name,needle,parent',[
    ('webtoon','친·인척','지원제외 대상'),
    ('suhyup','수탁자의 사임','퇴직연금의 특별중도해지사유'),
])
def test_heading_travels_with_list_item_without_rewriting_original(name,needle,parent):
    e,structure=fixture(name,needle)
    old=copy.deepcopy(e)
    result=attach([e],lambda *_: structure)
    assert e==old and result[0]['value_raw']==old['value_raw']
    catalog=build_catalog(result)
    heading=next(c for c in catalog if c['kind']=='scope_context' and parent in c['text_raw'])
    source=next(c for c in structure['contents'] if c['content_id']==heading['content_id'])
    assert heading['text_raw']==source['text_raw']
    assert heading['source_references']==source['source_references']
    assert heading['carrier_evidence_id']=='E1'
    assert heading['page']==source['source_references'][0]['page_number']
    shown=template_text(dict(evidence_ids=['E1'],values_raw=[e['value_raw']],abstained=False),result)
    assert parent in shown and needle in shown


@pytest.mark.parametrize('damage',['other_document','changed_title','no_membership','no_sections'])
def test_missing_or_conflicting_heading_evidence_is_not_guessed(damage):
    e,s=fixture('webtoon','친·인척')
    if damage=='other_document': e['document_id']='another-document'
    elif damage=='changed_title': s['sections'][0]['title_raw']='지원 가능'
    elif damage=='no_membership': s['sections'][0]['content_ids']=[s['sections'][0]['content_ids'][0]]
    else: s['sections']=[]
    assert contexts(e,s)==[]


def test_heading_citation_keeps_its_own_page():
    e,s=fixture('webtoon','친·인척')
    e['page']=3
    c=next(c for c in build_catalog(attach([e],lambda *_:s)) if c['kind']=='scope_context')
    assert c['page']==2
