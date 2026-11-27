# 역할: 저장·검색 분리 후에도 재실행 충돌 검사와 SQL 검색 범위·한도가 유지되는지 검사한다.
from types import SimpleNamespace

import pytest

from doc_understanding.rag import repository
from doc_understanding.rag.indexing import writer
from doc_understanding.rag.retrieval import repository as reader


@pytest.mark.parametrize('question,options', [('', {}), ('현금', {'run_ids': []})])
def test_empty_search_never_queries_database(monkeypatch, question, options):
    monkeypatch.setattr(reader, 'query', lambda _: pytest.fail('unexpected database query'))
    assert repository.search(question, **options) == []


def test_search_preserves_scope_escaping_and_limit(monkeypatch):
    statements = []
    monkeypatch.setattr(reader, 'query', lambda sql: statements.append(sql) or [])
    repository.search("현금 O'Reilly", run_id="run'1", limit=999)
    assert "e.run_id='run''1'" in statements[-1]
    assert "O''Reilly" in statements[-1]
    assert 'LIMIT 30' in statements[-1]
    assert "r.status NOT IN ('FAILED','RUNNING')" in statements[-1]
    repository.search('현금', limit=0)
    assert 'DISTINCT ON (d.document_id)' in statements[-1]
    assert 'LIMIT 1' in statements[-1]
    repository.search('현금', run_id='ignored', run_ids=['one', 'two'])
    assert "e.run_id IN ('one','two')" in statements[-1]
    assert 'ignored' not in statements[-1]


@pytest.mark.parametrize('digest,count,error', [
    ('other', 1, 'INDEX_SNAPSHOT_CONFLICT'),
    ('same', 0, 'INDEX_COVERAGE_MISMATCH'),
    ('same', 1, None),
])
def test_existing_index_checks_hash_and_coverage_without_write(monkeypatch, digest, count, error):
    # 검색 항목 수와 근거 저장본 항목 수를 차례로 묻는다. 둘 다 맞아야 한다.
    responses = iter([[{'snapshot_sha256': digest, 'index_version': writer.INDEX_VERSION}], [{'n': count}], [{'n': count}]])
    monkeypatch.setattr(writer, 'evidence_items', lambda _: [('table', 't', {'cells': []})])
    monkeypatch.setattr(writer, 'query', lambda _: next(responses))
    monkeypatch.setattr(writer, 'make_entries', lambda _: [{'entry_id': 'entry', 'text': '0'}])
    monkeypatch.setattr(writer, 'sql', lambda _: pytest.fail('unexpected index write'))
    if error:
        with pytest.raises(ValueError, match=error):
            repository.create_index('run', SimpleNamespace(document_id='doc'), 'same')
    else:
        repository.create_index('run', SimpleNamespace(document_id='doc'), 'same')


@pytest.mark.parametrize('existing', [False, True])
def test_index_write_keeps_transaction_and_failure_injection(monkeypatch, existing):
    statements = []
    monkeypatch.setattr(writer, 'query', lambda _: [
        {'snapshot_sha256': 'same', 'index_version': 'old'}] if existing else [])
    monkeypatch.setattr(writer, 'make_entries', lambda _: [{'entry_id': 'entry', 'text': '0'}])
    monkeypatch.setattr(writer, 'evidence_items', lambda _: [('table', 't', {'cells': []})])
    monkeypatch.setattr(writer, 'sql', statements.append)
    monkeypatch.setattr(writer.catalog, 'index_statements', lambda run_id, doc, entries: [f'-- catalog {run_id} {entries}'])
    repository.create_index('run', SimpleNamespace(document_id='doc'), 'same', fail=True)
    assert len(statements) == 1
    sql = statements[0]
    assert sql.startswith('BEGIN;') and sql.endswith('-- catalog run 1\nSELECT 1/0;\nCOMMIT;')
    assert 'INSERT INTO rag.search_entry' in sql and 'INSERT INTO rag.evidence_item' in sql
    assert ('DELETE FROM rag.search_entry' in sql) is existing
    assert ('DELETE FROM rag.evidence_item' in sql) is existing
    assert ('UPDATE rag.document_snapshot' in sql) is existing
