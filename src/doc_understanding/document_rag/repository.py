"""Reuse the project's guarded, local-only Docker/PostgreSQL repository transport."""
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from doc_understanding.domain.pipeline_contracts import CanonicalDocument
from doc_understanding.staging.local import ingest, js, literal, migrate as staging_migrate, query, sql
from doc_understanding.staging.prepare import load
from .document import validate_document
from .index import make_entries, terms
from .telemetry import traced_repository


ARTIFACT_ID = 'canonical-document'
INDEX_VERSION = 'document-rag/0.2'


def migrate():
    staging_migrate()
    sql('BEGIN;\n' + Path(__file__).with_name('schema.sql').read_text(encoding='utf8') + '\nCOMMIT;')


def store(manifest_path, document_path):
    manifest, ir, layout, artifacts = load(manifest_path)
    doc = CanonicalDocument.model_validate_json(Path(document_path).read_bytes())
    validate_document(doc)
    if doc.document_ir != ir:
        raise ValueError('DOCUMENT_IR_NOT_IDENTICAL')
    for ref in doc.source_artifact_refs:
        p = Path(manifest['run_dir']) / ref.path
        if hashlib.sha256(p.read_bytes()).hexdigest() != ref.sha256:
            raise ValueError('SOURCE_ARTIFACT_CHANGED')
    raw_hash = hashlib.sha256(Path(document_path).read_bytes()).hexdigest()
    entries = make_entries(doc)  # Fail before any DB writes if projection cannot be built.
    original_payload=json.loads(Path(document_path).read_text(encoding='utf8'))
    artifacts.append((ARTIFACT_ID, 'CanonicalDocument', Path(document_path), original_payload, None))
    migrate()
    result = ingest(ir, layout, Path(manifest['run_dir']), Path(manifest['source']),
                    f'{ir.document_id}:document-rag-v1:{raw_hash}', str(uuid4()), artifacts)
    run_id = result['run_id']
    create_index(run_id, doc, raw_hash)
    restored = fetch_document(run_id)
    if restored != doc:
        raise ValueError('CANONICAL_ROUNDTRIP_MISMATCH')
    result['checks'] = {**validate_document(restored), 'canonical_json_equal': True,
                        'source_artifact_hashes_exact': True,
                        'original_ir_json_equal': restored.document_ir == ir}
    result['search_entries'] = len(entries)
    result['document_sha256'] = raw_hash
    return result


def create_index(run_id, doc, raw_hash, fail=False):
    existing = query(f'SELECT snapshot_sha256,index_version FROM rag.document_snapshot WHERE run_id={literal(run_id)}')
    if existing:
        if existing[0]['snapshot_sha256'] != raw_hash:
            raise ValueError('INDEX_SNAPSHOT_CONFLICT')
        if existing[0]['index_version']==INDEX_VERSION:
            count = query(f'SELECT count(*) n FROM rag.search_entry WHERE run_id={literal(run_id)}')[0]['n']
            if count != len(make_entries(doc)):
                raise ValueError('INDEX_COVERAGE_MISMATCH')
            return
        statements=['BEGIN;',f'DELETE FROM rag.search_entry WHERE run_id={literal(run_id)};',
                    f'UPDATE rag.document_snapshot SET index_version={literal(INDEX_VERSION)} WHERE run_id={literal(run_id)};']
    else:
        statements = ['BEGIN;', 'INSERT INTO rag.document_snapshot(run_id,document_id,artifact_id,snapshot_sha256,index_version) VALUES ('
                      + ','.join(map(literal, (run_id, doc.document_id, ARTIFACT_ID, raw_hash,INDEX_VERSION))) + ');']
    for entry in make_entries(doc):
        statements.append('INSERT INTO rag.search_entry(run_id,entry_id,payload,search_text) VALUES ('
                          + ','.join((literal(run_id), literal(entry['entry_id']), js(entry), literal(entry['text']))) + ');')
    if fail:
        statements.append('SELECT 1/0;')
    sql('\n'.join(statements + ['COMMIT;']))


@traced_repository('fetch_document')
def fetch_document(run_id):
    rows = query(f"SELECT a.payload FROM staging.artifact a JOIN rag.document_snapshot d "
                 f"ON a.run_id=d.run_id AND a.artifact_id=d.artifact_id WHERE a.run_id={literal(run_id)}")
    if len(rows) != 1:
        raise ValueError('DOCUMENT_SNAPSHOT_NOT_FOUND')
    doc = CanonicalDocument.model_validate(rows[0]['payload'])
    validate_document(doc)
    return doc


@traced_repository('search')
def search(question, run_id=None, limit=8, *, run_ids=None):
    tokens = terms(question)
    if not tokens:
        return []
    token_array = 'ARRAY[' + ','.join(literal(t) for t in tokens) + ']::text[]'
    if run_ids is not None and not run_ids:
        return []
    restriction = ('AND e.run_id IN ('+','.join(literal(r) for r in run_ids)+')') if run_ids is not None else (
        f'AND e.run_id={literal(run_id)}' if run_id else '''AND e.run_id IN (
        SELECT DISTINCT ON (d.document_id) d.run_id FROM rag.document_snapshot d
        JOIN staging.processing_run pr ON pr.run_id=d.run_id
        WHERE pr.status NOT IN ('FAILED','RUNNING') ORDER BY d.document_id,d.created_at DESC,d.run_id DESC)''')
    # POSITION avoids LIKE wildcard semantics. Simple FTS is augmented with Hangul substrings.
    return query(f"""SELECT e.run_id,e.payload,
        (SELECT coalesce(sum(least(length(t),6)*(CASE
             WHEN position(t in lower(coalesce(e.payload->>'primary_text','')))>0 THEN 1.0 ELSE .2 END)),0)::float / {len(tokens)}
           FROM unnest({token_array}) t WHERE position(t in lower(e.search_text))>0)
         + ts_rank_cd(e.search_vector,plainto_tsquery('simple',{literal(question)})) AS score
        FROM rag.search_entry e JOIN staging.processing_run r ON r.run_id=e.run_id
        WHERE r.status NOT IN ('FAILED','RUNNING') {restriction}
        AND EXISTS(SELECT 1 FROM unnest({token_array}) t WHERE position(t in lower(e.search_text))>0)
        ORDER BY score DESC,e.run_id,e.entry_id LIMIT {max(1,min(int(limit),30))}""")


@traced_repository('cell_lookup')
def sql_value_probe(run_id, table_id, cell_id):
    """Actual JSONB SQL join between physical cells and derived table paths."""
    return query(f"""SELECT p->>'table_id' table_id,c->>'cell_id' cell_id,
         c->'original_text' value_raw,m->'row_path' row_node_ids,m->'column_path' column_node_ids
       FROM staging.artifact a,
         LATERAL jsonb_array_elements(a.payload->'physical_tables') p,
         LATERAL jsonb_array_elements(p->'cells') c,
         LATERAL jsonb_array_elements(a.payload->'table_structure') s,
         LATERAL jsonb_array_elements(s->'cell_mappings') m
       WHERE a.run_id={literal(run_id)} AND a.artifact_id={literal(ARTIFACT_ID)}
         AND p->>'table_id'={literal(table_id)} AND s->>'table_id'=p->>'table_id'
         AND c->>'cell_id'={literal(cell_id)} AND m->>'cell_id'=c->>'cell_id'""")
