CREATE SCHEMA IF NOT EXISTS staging;
CREATE TABLE IF NOT EXISTS staging.schema_revision (
 revision text PRIMARY KEY, installed_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS staging.source_document (
 source_id text PRIMARY KEY, original_path text NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS staging.document_version (
 version_id text PRIMARY KEY, source_id text NOT NULL REFERENCES staging.source_document,
 sha256 text NOT NULL CHECK(length(sha256)=64), bytes bigint NOT NULL,
 UNIQUE(source_id, sha256)
);
CREATE TABLE IF NOT EXISTS staging.processing_run (
 run_id text PRIMARY KEY, version_id text NOT NULL REFERENCES staging.document_version,
 idempotency_key text NOT NULL UNIQUE, status text NOT NULL,
 started_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
 metrics jsonb NOT NULL DEFAULT '{}', error text,
 fact_publication_allowed boolean NOT NULL DEFAULT false CHECK(NOT fact_publication_allowed)
);
CREATE TABLE IF NOT EXISTS staging.artifact (
 run_id text NOT NULL REFERENCES staging.processing_run,
 artifact_id text NOT NULL, kind text NOT NULL, sha256 text NOT NULL,
 local_path text NOT NULL, payload jsonb NOT NULL,
 PRIMARY KEY(run_id, artifact_id)
);
CREATE TABLE IF NOT EXISTS staging.page (
 run_id text NOT NULL REFERENCES staging.processing_run,
 page_id text NOT NULL, page_number integer NOT NULL, status text NOT NULL,
 diagnosis jsonb NOT NULL, PRIMARY KEY(run_id,page_id), UNIQUE(run_id,page_number)
);
CREATE TABLE IF NOT EXISTS staging.evidence (
 run_id text NOT NULL, evidence_id text NOT NULL, page_id text NOT NULL,
 kind text NOT NULL, payload jsonb NOT NULL,
 PRIMARY KEY(run_id,evidence_id),
 FOREIGN KEY(run_id,page_id) REFERENCES staging.page(run_id,page_id)
);
CREATE TABLE IF NOT EXISTS staging.content_block (
 run_id text NOT NULL, block_id text NOT NULL, payload jsonb NOT NULL,
 PRIMARY KEY(run_id,block_id),
 FOREIGN KEY(run_id,block_id) REFERENCES staging.evidence(run_id,evidence_id)
);
CREATE TABLE IF NOT EXISTS staging.relation_candidate (
 run_id text NOT NULL, relation_id text NOT NULL,
 source_ref text NOT NULL, target_ref text NOT NULL, payload jsonb NOT NULL,
 PRIMARY KEY(run_id,relation_id),
 FOREIGN KEY(run_id,source_ref) REFERENCES staging.evidence(run_id,evidence_id),
 FOREIGN KEY(run_id,target_ref) REFERENCES staging.evidence(run_id,evidence_id)
);
INSERT INTO staging.schema_revision(revision) VALUES ('023-local-staging-v1')
 ON CONFLICT DO NOTHING;
