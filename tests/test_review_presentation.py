"""공통 표시와 구조 검토 서비스의 원본 보존·출력·의존성 계약을 검사한다."""
import copy
import json

from tests.semantic_fixtures import compound


def nested_page():
    def box(x):
        return dict(x0=x, y0=x, x1=x + 10, y1=x + 10)

    def cell(cid, x):
        return dict(cell_id=cid, source_bbox=box(x), original_text='<raw>',
                    row_index=0, column_index=0, rowspan=1, colspan=1, span_refs=[])

    def table(tid, cid, x):
        return dict(table_id=tid, page_number=1, bounding_box=box(x), cells=[cell(cid, x)])

    doc = dict(document_ir=dict(pages=[dict(page_number=1, blocks=[], tables=[dict(cells=[
        dict(cell_id='native', source=dict(bbox=box(0))),
        dict(cell_id='unmatched', source=dict(bbox=box(80))),
    ])])]), physical_tables=[table('outer', 'parent', 0), table('inner', 'child', 2)],
        table_structure=[dict(cell_mappings=[dict(cell_id='child', row_path=['r'],
                                                  column_path=['c'])])])
    nested = dict(cells=[
        dict(page_number=1, parent_cell_id='native', containment_status='linked',
             child_table_ids=['inner']),
        dict(page_number=1, parent_cell_id='unmatched', containment_status='linked',
             child_table_ids=['inner']),
    ])
    return doc, nested


def test_shared_counts_match_nested_view_without_mutating_source():
    from doc_understanding.review.presentation import document, metrics

    doc, nested = nested_page()
    before = copy.deepcopy((doc, nested))
    view = document.page_view(doc, nested, 1)
    assert view['top_level_tables'] == 1 and view['nested_tables'] == 1
    assert view['unresolved_parent_links'] == ['unmatched']
    assert '&lt;raw&gt;' in view['html'] and '<raw>' not in view['html']

    row, = metrics.page_metrics(doc, nested)
    assert row['top_level_tables'] == view['top_level_tables']
    assert row['displayed_nested_tables'] == view['nested_tables']
    assert row['physical_cells'] == 2 and row['both_paths'] == 1
    assert (doc, nested) == before


def test_structure_cli_preserves_source_and_emits_review_artifacts(tmp_path):
    from doc_understanding.cli import main

    table, *_ = compound('survey')
    source = tmp_path / 'physical'
    source.mkdir()
    path = source / 'page-001-table-000.json'
    path.write_text(table.model_dump_json(), encoding='utf8')
    before = path.read_bytes()
    output = tmp_path / 'review'
    assert main(['semantic-structure', '--physical-dir', str(source),
                 '--output-dir', str(output)]) == 0
    run, = output.iterdir()
    summary = json.loads((run / 'summary.json').read_text('utf8'))
    assert summary['table_count'] == 1 and summary['llm_calls'] == 0
    assert summary['human_review'] == 'pending'
    assert summary['automatic_checks_are_not_semantic_accuracy'] is True
    assert (run / 'index.html').is_file()
    assert (run / 'structured-table-ir.schema.json').is_file()
    assert (run / 'page-001-table-000-vision-request.json').is_file()
    assert path.read_bytes() == before


def test_presentation_and_experiments_do_not_import_web_or_experiment_display():
    from tests.test_evaluation_boundaries import SOURCE, imports

    for directory in ('review/presentation', 'experiments'):
        for path in (SOURCE / directory).rglob('*.py'):
            for name in imports(path):
                assert not name.startswith('doc_understanding.web'), (path, name)
    for name in imports(SOURCE / 'services/structure_review.py'):
        assert not name.startswith(('doc_understanding.web', 'doc_understanding.experiments'))


def test_moved_renderer_still_rejects_frozen_logic_changes():
    import zipfile

    from doc_understanding.runtime.parser_freeze import equivalent_source, relocated_path
    from tests.test_evaluation_boundaries import ROOT

    old = 'src/doc_understanding/web/document_view.py'
    new = 'src/doc_understanding/review/presentation/document.py'
    assert relocated_path(old) == new
    with zipfile.ZipFile(ROOT / 'reports/parser-freeze-20261105/baseline.zip') as archive:
        baseline = archive.read(old)
    current = (ROOT / new).read_bytes()
    assert equivalent_source(baseline, current, old, new)
    # 표시용 부모 연결의 임계값도 단순 이동을 이유로 바꿀 수 없다.
    changed = current.replace(b'< 1.6', b'< 1.5')
    assert changed != current
    assert not equivalent_source(baseline, changed, old, new)
