"""b-01/b-03 + alternate rendering of KB development passages; no DB or models."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from doc_understanding.rag.agentic.documents import select
from doc_understanding.rag.agentic.publications import same_publication

FIXTURES = Path(__file__).parent/'fixtures/rag_p1'


@pytest.fixture
def publication():
    return json.loads((FIXTURES/'publications.json').read_text(encoding='utf8'))


@pytest.mark.parametrize('question', [
    'What was the percent change in real GDP in the fourth quarter of 2025 according to the BEA advance estimate?',
    'When will the second estimate of GDP for 2026 Q1 be released?',
])
def test_bea_alternate_renderings_are_both_searched(publication, question):
    docs, data = publication['documents'], publication['data']
    subjects = ['BEA'] if 'BEA' in question else ['GDP']
    assert select(subjects, docs, question=question)['status'] == 'needs_choice'
    result = select(subjects, docs, question=question,
                    equivalent=lambda pair: same_publication(*pair, data.get))
    assert result['status'] == 'selected'
    assert set(result['run_ids']) == {d['run_id'] for d in docs}


@pytest.mark.parametrize('change', ['body', 'date', 'publisher', 'title', 'missing'])
def test_same_title_is_not_enough(publication, change):
    docs, data = publication['documents'], publication['data']
    html = next(r for r in data.values() if r['media_type']=='text/html')
    if change == 'body':
        html['texts'] = [t.replace('1.4 percent', '1.8 percent') for t in html['texts']]
    elif change == 'date':
        html['texts'][0] = html['texts'][0].replace('February 20', 'February 21')
    elif change == 'publisher':
        html['uri'] = 'https://different.example/release'
    elif change == 'title':
        docs[0]['title'] += ' (revised)'
    else:
        data[docs[0]['run_id']] = None
    assert not same_publication(*docs, data.get)


def test_development_kb_rendering_and_changed_content():
    fixture = json.loads((FIXTURES/'dev-kb-passages.json').read_text(encoding='utf8'))
    # Rendering/URLs/header are constructed; the body is from the KB development document.
    title, date = 'KB퇴직연금정기예금 상품설명서', 'Published on November 1, 2026'
    docs = [dict(run_id=k, title=title, organization='KB') for k in ('pdf', 'html')]
    data = {k:dict(uri=f'https://publisher.example/document.{k}', media_type=media,
                   texts=[date,title,*fixture['texts']], kinds=['paragraph','heading']+['paragraph']*len(fixture['texts']))
            for k,media in [('pdf','application/pdf'),('html','text/html')]}
    assert same_publication(*docs, data.get)
    changed = deepcopy(data)
    changed['html']['texts'][-1] += ' 조건이 변경되었습니다.'
    assert not same_publication(*docs, changed.get)
