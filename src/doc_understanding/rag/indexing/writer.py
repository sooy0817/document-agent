# 역할: 검색 인덱스를 트랜잭션으로 생성·갱신하며 원본 해시와 버전·항목 수 충돌을 검사한다.
from doc_understanding.storage.documents import ARTIFACT_ID
from doc_understanding.storage.staging.local import js, literal, query, sql
from doc_understanding.rag import catalog
from doc_understanding.rag.evidence.source import evidence_items
from doc_understanding.rag.indexing.entries import make_entries

INDEX_VERSION = 'document-rag/0.10-parent-cell'


def create_index(run_id, doc, raw_hash, fail=False):
    existing = query(f'SELECT snapshot_sha256,index_version FROM rag.document_snapshot WHERE run_id={literal(run_id)}')
    if existing:
        if existing[0]['snapshot_sha256'] != raw_hash:
            raise ValueError('INDEX_SNAPSHOT_CONFLICT')
        if existing[0]['index_version']==INDEX_VERSION:
            count = query(f'SELECT count(*) n FROM rag.search_entry WHERE run_id={literal(run_id)}')[0]['n']
            items = query(f'SELECT count(*) n FROM rag.evidence_item WHERE run_id={literal(run_id)}')[0]['n']
            if count != len(make_entries(doc)) or items != len(evidence_items(doc)):
                raise ValueError('INDEX_COVERAGE_MISMATCH')
            return
        statements=['BEGIN;',f'DELETE FROM rag.search_entry WHERE run_id={literal(run_id)};',
                    f'DELETE FROM rag.evidence_item WHERE run_id={literal(run_id)};',
                    f'UPDATE rag.document_snapshot SET index_version={literal(INDEX_VERSION)} WHERE run_id={literal(run_id)};']
    else:
        statements = ['BEGIN;', 'INSERT INTO rag.document_snapshot(run_id,document_id,artifact_id,snapshot_sha256,index_version) VALUES ('
                      + ','.join(map(literal, (run_id, doc.document_id, ARTIFACT_ID, raw_hash,INDEX_VERSION))) + ');']
    entries = make_entries(doc)
    for entry in entries:
        statements.append('INSERT INTO rag.search_entry(run_id,entry_id,payload,search_text) VALUES ('
                          + ','.join((literal(run_id), literal(entry['entry_id']), js(entry), literal(entry['text']))) + ');')
    # 근거 저장본(표·본문 단위)을 검색 항목과 같은 트랜잭션에 넣는다. 한쪽만 바뀌는 일이 없다.
    for kind, item_id, payload in evidence_items(doc):
        statements.append('INSERT INTO rag.evidence_item(run_id,kind,item_id,payload) VALUES ('
                          + ','.join((literal(run_id), literal(kind), literal(item_id), js(payload))) + ');')
    # 문서 목록표(이름·쪽수·현재 검색 대상 실행)도 같은 트랜잭션에서 갱신한다.
    statements += catalog.index_statements(run_id, doc, len(entries))
    if fail:
        statements.append('SELECT 1/0;')
    sql('\n'.join(statements + ['COMMIT;']))
