# 역할: 기존 적재·조회 진입점을 유지한다. 원본 검증 → 스키마 → 저장 → 색인 → 재조회 순서를 연결한다.
from pathlib import Path
from doc_understanding.storage.staging.local import migrate as staging_migrate, sql
from doc_understanding.storage import documents
from doc_understanding.parser.assembly.document import validate_document
from doc_understanding.rag.indexing.entries import make_entries
from doc_understanding.rag.indexing.writer import create_index as create_index, INDEX_VERSION as INDEX_VERSION
from doc_understanding.rag.retrieval.repository import (
    fetch_document as fetch_document, search as search, search_many as search_many,
    sql_nested_probe as sql_nested_probe, sql_value_probe as sql_value_probe,
    evidence_source as evidence_source, table_ids as table_ids,
)

ARTIFACT_ID = documents.ARTIFACT_ID


def migrate():
    staging_migrate()
    sql('BEGIN;\n' + Path(__file__).with_name('schema.sql').read_text(encoding='utf8') + '\nCOMMIT;')



def store(manifest_path, document_path, *, migrate_schema=True):
    # 기존 적재 진입점과 반환 형식을 유지하며 저장 후 원본 재조회 일치 여부를 검사한다.
    prepared = documents.prepare(manifest_path, document_path)
    doc, raw_hash = prepared.document, prepared.sha256
    # 검색 항목 생성이 실패하면 원본 저장을 포함한 DB 쓰기도 시작하지 않는다.
    entries = make_entries(doc)
    # 작업자 여러 개가 동시에 적재할 때는 시작할 때 한 번만 스키마를 준비하고 여기서는 건너뛴다.
    if migrate_schema:
        migrate()
    result = documents.persist(
        prepared, key=f'{doc.document_ir.document_id}:document-rag-v1:{raw_hash}')
    run_id = result['run_id']
    create_index(run_id, doc, raw_hash)
    restored = fetch_document(run_id)
    if restored != doc:
        raise ValueError('CANONICAL_ROUNDTRIP_MISMATCH')
    result['checks'] = {**validate_document(restored), 'canonical_json_equal': True,
                        'source_artifact_hashes_exact': True,
                        'original_ir_json_equal': restored.document_ir == doc.document_ir}
    result['search_entries'] = len(entries)
    result['document_sha256'] = raw_hash
    return result
