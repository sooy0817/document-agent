# 역할: 저장 분리 후에도 원본 검증과 DB 쓰기 순서·반환 계약이 유지되는지 확인한다.
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from doc_understanding.storage import documents
from doc_understanding.rag import repository
from doc_understanding.parser.assembly.document import build_from_run


@pytest.fixture
def source(tmp_path, monkeypatch):
    # 기존 동결 실행을 읽고 저장할 문서 JSON만 임시 경로에 만든다.
    run = Path(__file__).parents[1] / 'data/staging-mvp/1a90fc75-f5eb-4c3e-8438-7347ce83547c'
    doc = build_from_run(run)
    path = tmp_path / 'document.json'
    path.write_text(doc.model_dump_json(), encoding='utf8')
    manifest = {'run_dir': str(run), 'source': 'original.pdf'}
    monkeypatch.setattr(documents, 'load', lambda _: (manifest, doc.document_ir, None, []))
    return path, doc


def test_prepare_preserves_json_and_persist_arguments(source, monkeypatch):
    # DB 전송 직전까지 JSON 원문·해시·재실행 키가 변하지 않아야 한다.
    path, doc = source
    prepared = documents.prepare('manifest', path)
    assert prepared.document == doc
    assert prepared.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert prepared.artifacts[-1][3] == json.loads(path.read_text(encoding='utf8'))
    calls = []
    options = []
    monkeypatch.setattr(documents, 'ingest',
                        lambda *args, **kw: (calls.append(args), options.append(kw)) and {'run_id': 'saved'})
    assert documents.persist(prepared, key='existing-key') == {'run_id': 'saved'}
    # 색인 경로는 읽는 곳이 없는 세부 표를 쓰지 않고, 문서 기록의 내용만 DB에 넣는다.
    assert options[0] == {'detail': False, 'payload_ids': {documents.ARTIFACT_ID}}
    assert calls[0][:5] == (doc.document_ir, None, Path(prepared.manifest['run_dir']),
                           Path('original.pdf'), 'existing-key')
    assert calls[0][6] == prepared.artifacts


def test_changed_source_artifact_is_rejected(source):
    # 출처 파일이 일치하지 않으면 저장 준비 단계에서 중단한다.
    path, doc = source
    ref = doc.source_artifact_refs[0].model_copy(update={'sha256': '0' * 64})
    changed = doc.model_copy(update={'source_artifact_refs': (ref,) + doc.source_artifact_refs[1:]})
    path.write_text(changed.model_dump_json(), encoding='utf8')
    with pytest.raises(ValueError, match='SOURCE_ARTIFACT_CHANGED'):
        documents.prepare('manifest', path)


def test_projection_failure_does_not_write_db(source, monkeypatch):
    # 검색 항목을 만들 수 없으면 스키마 생성과 원본 저장도 실행하지 않는다.
    path, _ = source
    def fail(_):
        raise ValueError('projection failed')
    monkeypatch.setattr(repository, 'make_entries', fail)
    monkeypatch.setattr(repository, 'migrate', lambda: pytest.fail('unexpected schema write'))
    monkeypatch.setattr(documents, 'persist', lambda *a, **k: pytest.fail('unexpected source write'))
    with pytest.raises(ValueError, match='projection failed'):
        repository.store('manifest', path)


def test_store_order_result_and_roundtrip_guard(source, monkeypatch):
    # 원본 저장 후 인덱스를 만들며 재조회 불일치는 계속 오류로 처리한다.
    path, doc = source
    events = []
    monkeypatch.setattr(repository, 'migrate', lambda: events.append('migrate'))
    def persist(prepared, *, key):
        assert key == f'{doc.document_ir.document_id}:document-rag-v1:{prepared.sha256}'
        events.append('persist')
        return {'run_id': 'saved'}
    monkeypatch.setattr(documents, 'persist', persist)
    monkeypatch.setattr(repository, 'create_index', lambda *a: events.append('index'))
    monkeypatch.setattr(repository, 'fetch_document', lambda _: doc)
    result = repository.store('manifest', path)
    assert events == ['migrate', 'persist', 'index']
    assert result['checks']['canonical_json_equal'] is True
    assert result['checks']['original_ir_json_equal'] is True
    assert result['search_entries'] == len(repository.make_entries(doc))
    assert result['document_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(repository, 'fetch_document', lambda _: SimpleNamespace())
    with pytest.raises(ValueError, match='CANONICAL_ROUNDTRIP_MISMATCH'):
        repository.store('manifest', path)


def test_store_without_detail_skips_unread_tables_and_keeps_the_rest(monkeypatch):
    # ADR-080 1단계: evidence·content_block·relation_candidate만 빠지고 나머지 문장은 한 글자도 다르지 않다.
    from doc_understanding.parser.pipeline import load
    from doc_understanding.storage.staging import local

    run = Path(__file__).parents[1] / 'data/staging-mvp/1a90fc75-f5eb-4c3e-8438-7347ce83547c'
    manifest, ir, layout, artifacts = load(run / 'staging-manifest.json')
    monkeypatch.setattr(local, 'query', lambda _: [])
    sent = {}

    def run_ingest(detail):
        scripts = []
        monkeypatch.setattr(local, 'sql', lambda statement: scripts.append(statement) or '')
        result = local.ingest(ir, layout, Path(manifest['run_dir']), Path(manifest['source']), 'key', 'run-1', artifacts,
                              detail=detail)
        assert result['run_id'] == 'run-1' and not result['duplicate']
        sent[detail] = [line for script in scripts for line in script.split('\n')]

    run_ingest(True)
    run_ingest(False)
    unread = ('INSERT INTO staging.evidence ', 'INSERT INTO staging.content_block ', 'INSERT INTO staging.relation_candidate ')
    assert any(line.startswith(unread[0]) for line in sent[True]) and any(line.startswith(unread[1]) for line in sent[True])
    assert not [line for line in sent[False] if line.startswith(unread)]
    assert sent[False] == [line for line in sent[True] if not line.startswith(unread)]
    assert sum(line.startswith('INSERT INTO staging.artifact ') for line in sent[False]) == len(artifacts)
    assert sum(map(len, sent[False])) < sum(map(len, sent[True]))


def test_only_the_document_record_keeps_its_content_and_every_artifact_keeps_its_hash(source, monkeypatch):
    # ADR-080 2단계: 다른 artifact는 줄과 해시는 남고 내용만 빈 값이다. 문서 기록의 문장은 한 글자도 다르지 않다.
    from doc_understanding.storage.staging import local

    path, doc = source
    monkeypatch.undo()  # source 고정구의 load 대체를 풀고 실제 실행 기록을 읽는다
    run = Path(__file__).parents[1] / 'data/staging-mvp/1a90fc75-f5eb-4c3e-8438-7347ce83547c'
    prepared = documents.prepare(run / 'staging-manifest.json', path)
    sent = {}

    def run_ingest(name, **options):
        scripts = []
        monkeypatch.setattr(local, 'query', lambda _: [])
        monkeypatch.setattr(local, 'sql', lambda statement: scripts.append(statement) or '')
        local.ingest(prepared.ir, prepared.layout, Path(prepared.manifest['run_dir']), Path(prepared.manifest['source']),
                     'key', 'run-1', prepared.artifacts, detail=False, **options)
        sent[name] = [line for script in scripts for line in script.split('\n') if line.startswith('INSERT INTO staging.artifact ')]

    run_ingest('all')
    run_ingest('record_only', payload_ids={documents.ARTIFACT_ID})
    assert len(sent['all']) == len(sent['record_only']) == len(prepared.artifacts) > 1
    record = [n for n, a in enumerate(prepared.artifacts) if a[0] == documents.ARTIFACT_ID]
    assert len(record) == 1 and sent['all'][record[0]] == sent['record_only'][record[0]]
    for n, (full, slim) in enumerate(zip(sent['all'], sent['record_only'])):
        if n != record[0]:
            assert slim.endswith("'{}'::jsonb);") and len(slim) < len(full)
            # 종류·해시·경로는 같다(재실행 때 해시 대조가 그대로 된다).
            assert slim.split(",'{")[0][:200] == full[:200].split(",'{")[0][:200] or slim[:150] == full[:150]
    assert sum(map(len, sent['record_only'])) * 1.5 < sum(map(len, sent['all']))
