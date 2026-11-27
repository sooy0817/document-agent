"""평가의 실행 의존성과 원본 보존 계약을 검증한다."""
import ast
import copy
import importlib.util
from pathlib import Path

from doc_understanding.evaluation.parser.snapshots import preservation


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'src/doc_understanding'


def imports(path):
    module = '.'.join(path.relative_to(ROOT / 'src').with_suffix('').parts)
    package = module.removesuffix('.__init__') if path.name == '__init__.py' else module.rpartition('.')[0]
    for node in ast.walk(ast.parse(path.read_bytes())):
        if isinstance(node, ast.ImportFrom):
            name = importlib.util.resolve_name('.' * node.level + (node.module or ''), package) if node.level else node.module or ''
            yield name
            yield from (name + '.' + alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)


def test_parser_evaluators_do_not_load_execution_or_model_layers():
    # 정답 참조가 추출·패치·모델 실행으로 흘러가지 않도록 의존성 방향을 고정한다.
    forbidden = ('doc_understanding.experiments', 'doc_understanding.agents',
                 'doc_understanding.parser', 'doc_understanding.web',
                 'doc_understanding.runtime', 'subprocess', 'socket')
    for path in (SOURCE / 'evaluation/parser').rglob('*.py'):
        for name in imports(path):
            assert not any(name == prefix or name.startswith(prefix + '.') for prefix in forbidden), (path, name)


def test_runtime_validation_and_review_do_not_depend_on_scoring():
    for directory in ('validation', 'review'):
        for path in (SOURCE / directory).rglob('*.py'):
            assert not any(name.startswith('doc_understanding.evaluation') for name in imports(path)), path


def test_preservation_rejects_changed_paths_text_and_accepted_links_without_mutation():
    document = dict(physical_tables=[dict(cells=[dict(cell_id='c', original_text='0')])],
                    table_structure=[dict(cell_mappings=[dict(cell_id='c', row_path=['r'], column_path=['h'], evidence_state='inferred')])],
                    document_ir=dict(pages=[dict(blocks=[dict(spans=[dict(span_id='s', text_raw='0')])])]))
    links = dict(cells=[dict(parent_cell_id='c', containment_status='linked', items=['child'])])
    original = copy.deepcopy((document, links))
    assert preservation(document, copy.deepcopy(document), links, copy.deepcopy(links))['passed']
    changed = copy.deepcopy(document)
    changed['table_structure'][0]['cell_mappings'][0]['row_path'] = ['wrong']
    assert not preservation(document, changed, links, links)['semantic_paths_unchanged']
    changed = copy.deepcopy(document)
    changed['document_ir']['pages'][0]['blocks'][0]['spans'][0]['text_raw'] = '1'
    assert not preservation(document, changed, links, links)['native_text_preserved']
    changed_links = copy.deepcopy(links)
    changed_links['cells'][0]['items'] = []
    result = preservation(document, document, links, changed_links)
    assert not result['passed'] and result['changed_accepted_parent_ids'] == ['c']
    assert (document, links) == original


def test_rag_evaluators_only_read_saved_results():
    # RAG 평가는 저장된 실행 결과와 문서 계약만 읽는다. 검색·모델·웹 실행 계층을 불러오지 않는다.
    forbidden = ('doc_understanding.rag', 'doc_understanding.agents', 'doc_understanding.services',
                 'doc_understanding.parser', 'doc_understanding.web', 'doc_understanding.runtime',
                 'doc_understanding.experiments', 'subprocess', 'socket')
    for path in [*(SOURCE / 'evaluation/rag').rglob('*.py'), *(SOURCE / 'evaluation/mapping').rglob('*.py')]:
        for name in imports(path):
            assert not any(name == prefix or name.startswith(prefix + '.') for prefix in forbidden), (path, name)
