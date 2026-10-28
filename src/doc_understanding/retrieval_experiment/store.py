"""Isolated pgvector search projections. No writes to the canonical NH database."""
import json
import math
from pathlib import Path
import subprocess
from doc_understanding.staging.local import literal, js
from .embeddings import MODEL, DIMENSIONS

ROOT = Path(__file__).resolve().parents[3]


def sql(statement):
    result = subprocess.run(['docker', 'compose', '-f', str(ROOT/'infra/vector/compose.yaml'),
        'exec', '-T', 'vector', 'psql', '-X', '-U', 'retrieval', '-d', 'retrieval_experiment',
        '-v', 'ON_ERROR_STOP=1', '-At'], input='SET standard_conforming_strings=on;\n'+statement,
        encoding='utf8', capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError('VECTOR_POSTGRES_STATEMENT_FAILED')
    return '\n'.join(line for line in result.stdout.splitlines() if line != 'SET')


def query(statement):
    return json.loads(sql("SELECT coalesce(json_agg(q),'[]'::json) FROM ("+statement+') q;'))


def migrate():
    if sql('SELECT current_database();').strip() != 'retrieval_experiment':
        raise ValueError('EXPERIMENT_DATABASE_GUARD')
    sql('''CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS table_retrieval;
CREATE TABLE IF NOT EXISTS table_retrieval.projection (
 corpus_id text NOT NULL, projection_id text NOT NULL, kind text NOT NULL,
 run_id text NOT NULL, document_id text NOT NULL, table_id text NOT NULL,
 institution text, provenance text NOT NULL, snapshot_sha256 text NOT NULL,
 text_sha256 text NOT NULL, payload jsonb NOT NULL,
 model text NOT NULL, embedding vector(1536) NOT NULL,
 PRIMARY KEY(corpus_id,projection_id));
CREATE INDEX IF NOT EXISTS projection_lookup ON table_retrieval.projection(corpus_id,kind,institution,table_id);
CREATE TABLE IF NOT EXISTS table_retrieval.corpus (
 corpus_id text PRIMARY KEY, manifest jsonb NOT NULL, created_at timestamptz DEFAULT now());''')


def vector_literal(vector):
    if len(vector) != DIMENSIONS or not all(isinstance(v, (float, int)) and math.isfinite(v) for v in vector):
        raise ValueError('INVALID_SEARCH_VECTOR')
    return literal(json.dumps(vector, allow_nan=False))+'::vector'


def ingest(corpus_id, manifest, projections, vectors):
    migrate()
    existing = query(f'SELECT manifest FROM table_retrieval.corpus WHERE corpus_id={literal(corpus_id)}')
    if existing:
        if existing[0]['manifest'] != manifest:
            raise ValueError('IMMUTABLE_EXPERIMENT_CORPUS_CONFLICT')
        restored = query(f'SELECT projection_id,payload,model,vector_dims(embedding) dimensions '
                         f'FROM table_retrieval.projection WHERE corpus_id={literal(corpus_id)}')
        if len(restored) != len(projections):
            raise ValueError('PROJECTION_COVERAGE_MISMATCH')
        expected = {p.projection_id:p.model_dump() for p in projections}
        if any(r['payload'] != expected.get(r['projection_id']) or r['model'] != MODEL
               or r['dimensions'] != DIMENSIONS for r in restored):
            raise ValueError('IMMUTABLE_PROJECTION_PAYLOAD_OR_MODEL_CONFLICT')
        return dict(reused=True, projections=len(projections))
    statements = ['BEGIN;']
    for p, vector in zip(projections, vectors, strict=True):
        fields = (corpus_id, p.projection_id, p.kind, p.run_id, p.document_id, p.table_id,
                  p.institution, p.provenance, p.snapshot_sha256, p.text_sha256)
        statements.append('INSERT INTO table_retrieval.projection VALUES ('+
            ','.join(literal(v) for v in fields)+','+js(p.model_dump())+','+literal(MODEL)+','+vector_literal(vector)+');')
    statements += [f'INSERT INTO table_retrieval.corpus(corpus_id,manifest) VALUES ({literal(corpus_id)},{js(manifest)});', 'COMMIT;']
    sql('\n'.join(statements))
    return dict(reused=False, projections=len(projections))


def search(corpus_id, vector, *, kind='table', institution=None, table_ids=None, limit=10):
    restrict = f'AND institution={literal(institution)}' if institution else ''
    if table_ids is not None:
        if not table_ids:
            return []
        restrict += ' AND table_id IN ('+','.join(literal(t) for t in table_ids)+')'
    v = vector_literal(vector)
    rows = query(f'''SELECT projection_id,run_id,document_id,table_id,payload,
 1-(embedding <=> {v}) score FROM table_retrieval.projection
 WHERE corpus_id={literal(corpus_id)} AND kind={literal(kind)}
 AND provenance='source_rule' AND model={literal(MODEL)} {restrict}
 ORDER BY embedding <=> {v},projection_id LIMIT {max(1,min(int(limit),100))}''')
    return rows
