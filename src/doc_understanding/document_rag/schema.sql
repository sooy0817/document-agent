CREATE SCHEMA IF NOT EXISTS rag;
CREATE TABLE IF NOT EXISTS rag.document_snapshot (
 run_id text PRIMARY KEY REFERENCES staging.processing_run,
 document_id text NOT NULL,
 artifact_id text NOT NULL,
 snapshot_sha256 text NOT NULL CHECK(length(snapshot_sha256)=64),
 index_version text NOT NULL DEFAULT 'document-rag/0.1',
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(run_id,artifact_id) REFERENCES staging.artifact(run_id,artifact_id)
);
CREATE TABLE IF NOT EXISTS rag.search_entry (
 run_id text NOT NULL REFERENCES rag.document_snapshot,
 entry_id text NOT NULL,
 payload jsonb NOT NULL,
 search_text text NOT NULL,
 search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple',search_text)) STORED,
 PRIMARY KEY(run_id,entry_id)
);
CREATE INDEX IF NOT EXISTS rag_search_vector ON rag.search_entry USING gin(search_vector);
INSERT INTO staging.schema_revision(revision) VALUES ('027-document-rag-v1') ON CONFLICT DO NOTHING;
