-- 역할: 검색 항목별 모델·입력 버전과 pgvector 벡터를 저장한다. 원문은 기존 저장소에 남긴다.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS rag.entry_embedding (
 run_id text NOT NULL,
 entry_id text NOT NULL,
 profile_id text NOT NULL CHECK(length(profile_id)=64),
 profile jsonb NOT NULL,
 dimensions integer NOT NULL CHECK(dimensions BETWEEN 1 AND 3072),
 input_sha256 text NOT NULL CHECK(length(input_sha256)=64),
 input_text text NOT NULL,
 index_version text NOT NULL,
 embedding vector NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(run_id,entry_id,profile_id),
 FOREIGN KEY(run_id,entry_id) REFERENCES rag.search_entry(run_id,entry_id) ON DELETE CASCADE,
 CHECK(vector_dims(embedding)=dimensions),
 CHECK(vector_norm(embedding)>0)
);
CREATE INDEX IF NOT EXISTS rag_embedding_profile ON rag.entry_embedding(profile_id,run_id);
INSERT INTO staging.schema_revision(revision) VALUES ('028-rag-embeddings') ON CONFLICT DO NOTHING;
