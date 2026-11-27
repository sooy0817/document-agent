from copy import deepcopy
import json
from pathlib import Path

import pytest

from doc_understanding.agents.role_proposals import classify, propose


def test_explicit_roles_and_ambiguous_body():
    assert classify('(단위 : 백만원, %)', 12, 12)[:2] == ('unit', 'inferred')
    assert classify('주) 별첨 참조', 12, 12)[0] == 'note'
    assert classify('Arbitrary title', 18, 12)[0] == 'heading'
    assert classify('Some content', 12, 12)[:2] == ('paragraph', 'unresolved')
    assert classify('단위당 생산성은 증가', 12, 12)[0] == 'paragraph'


def test_two_tables_do_not_force_unique_scope():
    box = dict(x0=0,y0=50,x1=200,y1=100)
    blocks = [dict(block_id='note', kind='text', text_raw='주) 설명', spans=[],
                   source={'bbox':dict(x0=0,y0=110,x1=200,y1=120)})]
    blocks += [dict(block_id=t, kind='table_candidate', text_raw=None, source={'bbox':box}) for t in ['a','b']]
    bundle = {'artifacts':{'document-ir':{'payload':{'pages':[{'page_number':1,'blocks':blocks}]}},
                          'layout':{'payload':{'schema_version':'document-layout/0.1','relations':[]}}}}
    before = deepcopy(bundle)
    result = propose(bundle,[1])
    note = next(r for r in result if r.role == 'note')
    assert note.target_candidates == ('a','b')
    assert note.relation_state == note.scope_state == 'unresolved'
    assert bundle == before


def test_real_development_pages_preserve_source_and_split_lines():
    path = Path('data/document-json-review/current-full.json')
    if not path.exists():
        pytest.skip('private development artifact absent')
    bundle = json.loads(path.read_text(encoding='utf-8'))
    before = deepcopy(bundle)
    items = propose(bundle, [2,3])
    assert len(items) == 8
    for page in [2,3]:
        subset = [i for i in items if i.page == page]
        assert [i.role for i in subset] == ['heading','unit','table','note']
        assert len(next(i for i in subset if i.role == 'table').structured_refs) == 1
        assert all(i.review == 'unreviewed' and i.scope_state == 'unresolved' for i in subset)
    assert items[0].block_id == items[1].block_id
    assert items[0].span_ids != items[1].span_ids
    assert bundle == before
