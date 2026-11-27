"""Cross-domain runtime regressions. All model/DB traffic is injected locally."""
import json

import pytest
from tests.rag_assessment_helpers import receipt

from doc_understanding.rag import service
from doc_understanding.rag.generation.answer import generate
from doc_understanding.rag.batch import inventory
from doc_understanding.rag.generation.narrative import build_catalog, requirements
from doc_understanding.runtime.catalogs import campaign_manifest, sample_catalog
from doc_understanding.agents.workflows.campaign.public_sources import discover
from doc_understanding.agents.workflows.campaign.public_download import collect_public_pdfs
from doc_understanding.agents.workflows.campaign.store import CampaignStore


def evidence(name='연구소', value='정상', **extra):
    return dict(evidence_id='E1', source_name=name, page=1, table_id='T', cell_id='C',
                value_raw=value, row_path=['센서A'], column_path=['상태'],
                lookup_eligible=True, **extra)


def answer(payload, key, timeout):
    data=json.loads(payload['input'][1]['content'])
    return dict(status='completed', model=payload['model'], usage={'input_tokens': 10, 'output_tokens': 5},
                output=[dict(type='message', content=[dict(type='output_text', text=json.dumps(
                    dict(evidence_ids=['E1'], values_raw=['정상'], abstained=False,
                         assessment=receipt(data['question'],data['sources']))))])])


def test_period_can_be_a_row_and_text_values_remain_valid(tmp_path):
    e = evidence()
    e['row_path'] = ['2024', '센서A']
    assert service.validate_evidence([e], '2024 센서A 상태')['sufficient']
    result = generate('센서 상태', [e], tmp_path/'answer', allow_external=True,
        model='configured-model', input_usd_per_million=1, output_usd_per_million=3,
        transport=answer)
    assert result['selection']['values_raw'] == ['정상']
    assert result['model'] == 'configured-model'
    assert result['estimated_cost_usd'] == 25 / 1_000_000
    assert '재무' not in result['answer']


@pytest.mark.parametrize('rates', [{}, {'input_usd_per_million': float('nan'), 'output_usd_per_million': 3}])
def test_unknown_model_needs_valid_cost_assumptions(tmp_path, rates):
    with pytest.raises(ValueError, match='EXPLICIT_MODEL_PRICING_REQUIRED'):
        generate('status', [evidence()], tmp_path/'answer', allow_external=True,
                 model='configured-model', transport=lambda *_: pytest.fail('No call'), **rates)


def test_context_sources_are_available_independently_of_question_words():
    e = evidence()
    e['nested_context'] = {'prose': [{'text_raw': '센서A는 실내인 경우 적용', 'content_id': 'P'}]}
    catalog = build_catalog([e])
    required = requirements('센서A 적용 조건', catalog)
    assert required['context_source_ids'] == ['P1']
    assert requirements('Explain applicability', catalog)==required


def test_batch_inventory_has_no_year_institution_or_summary_preference(tmp_path):
    root = tmp_path/'inputs'
    for group in ['남평', 'medical', 'engineering', 'holdout']:
        (root/group).mkdir(parents=True)
        (root/group/'record.pdf').write_bytes(group.encode())
    result = inventory(root, tmp_path/'index.json', limit=5, core=0, exclude_paths=['holdout'])
    assert {d['group'] for d in result['documents']} == {'남평', 'medical', 'engineering'}
    assert result['pattern'] == '**/*.pdf'
    assert 'year' not in result


def test_catalogs_are_opt_in_and_paths_cannot_escape(tmp_path, monkeypatch):
    for key in ['DOCUMENT_SAMPLE_CATALOG', 'DOCUMENT_CAMPAIGN_MANIFEST', 'DOCUMENT_PUBLIC_SOURCE_CATALOG']:
        monkeypatch.delenv(key, raising=False)
    assert sample_catalog(tmp_path) == {} and campaign_manifest(tmp_path) is None
    store = CampaignStore(tmp_path/'store')
    cid = store.create('generic')
    discover(store, cid, fetch=lambda *_: pytest.fail('No configured source'))
    assert collect_public_pdfs(store, cid, fetch_page=lambda *_: pytest.fail('No configured source')) == 0
    config = tmp_path/'samples.json'
    config.write_text(json.dumps([dict(id='x', name='sample', path='../outside.pdf')]))
    monkeypatch.setenv('DOCUMENT_SAMPLE_CATALOG', str(config))
    with pytest.raises((ValueError, FileNotFoundError)):
        sample_catalog(tmp_path)


def test_generic_public_source_policy_is_not_bound_to_finance(tmp_path, monkeypatch):
    import fitz
    policy = dict(id='research', organization='Research Lab', domain='science',
                  url='https://example.org/reports', host='example.org', path='/report',
                  rights_markers=['Open research reuse'])
    path = tmp_path/'sources.json'
    path.write_text(json.dumps([policy]))
    monkeypatch.setenv('DOCUMENT_PUBLIC_SOURCE_CATALOG', str(path))
    store = CampaignStore(tmp_path/'store')
    cid = store.create('science')
    discover(store, cid, fetch=lambda _: '<a href="/report?id=1">Materials</a>')
    with fitz.open() as pdf:
        pdf.new_page()
        raw = pdf.tobytes()
    assert collect_public_pdfs(store, cid,
        fetch_page=lambda _: '<p>Open research reuse</p><a href="/a.pdf">PDF</a>',
        fetch_pdf=lambda _: raw) == 1
    assert store.get(cid)['samples'][0]['role'] == 'development'


def test_review_defaults_do_not_load_historical_catalogs(tmp_path):
    from doc_understanding.review.human.app import create_app
    app = create_app(cases=tmp_path/'cases', output=tmp_path/'events', meaning_cases=tmp_path/'meaning')
    endpoints = {r.path: r.endpoint for r in app.routes}
    assert endpoints['/api/precheck']()['legacy_checks'] == []
    assert endpoints['/api/meaning-review-pack']()['items'] == []
    assert endpoints['/api/meaning']() == []


def test_text_cell_content_survives_evidence_limit(tmp_path):
    from doc_understanding.parser.extraction.html_adapter import prepare_html
    from doc_understanding.parser.assembly.document import build_from_run
    from doc_understanding.rag.index import make_entries, evidence_pack
    source = tmp_path/'operations.html'
    source.write_text('<table><tr><th>Task</th><th>Description</th></tr>'
        '<tr><td>A</td><td>routine inspection</td></tr>'
        '<tr><td>B</td><td>replace damaged sensor</td></tr></table>', encoding='utf8')
    run, _ = prepare_html(source, tmp_path/'run')
    doc = build_from_run(run)
    hits = [e for e in make_entries(doc) if e['kind'] == 'table']
    selected = evidence_pack(doc, hits, 'damaged sensor', max_cells=1)
    cells = [e for e in selected if e.get('table_id')]
    assert len(cells) == 1 and cells[0]['value_raw'] == 'replace damaged sensor'


def test_sampling_exclusions_are_explicit_and_propagate_to_duplicates():
    from doc_understanding.evaluation.sample_unseen import select
    from tests.test_tree_accuracy import inventory as rows, FREEZE
    items = rows(2)
    items[0]['institution_name'] = '남평농협'
    assert select(items, FREEZE, count=2)['selected_count'] == 2
    items[1]['sha256'] = items[0]['sha256']
    policy = {**FREEZE, 'excluded_institution_ids': [items[0]['institution_id']]}
    assert select(items, policy, count=2)['selected_count'] == 0
