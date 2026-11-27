-- 역할: 원본 실행을 참조하는 검색 스냅샷·문자열 항목·FTS 인덱스를 정의한다.
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
-- 근거 저장본: 표 하나(셀·행열 경로·담긴 위치)와 본문 내용 하나를 ID로 읽는다. 질문 때 문서 전체를 읽지 않기 위한 것이며
-- 검색 항목과 같은 트랜잭션에서 같은 문서로 만든다.
CREATE TABLE IF NOT EXISTS rag.evidence_item (
 run_id text NOT NULL REFERENCES rag.document_snapshot,
 kind text NOT NULL CHECK(kind IN ('table','content')),
 item_id text NOT NULL,
 payload jsonb NOT NULL,
 PRIMARY KEY(run_id,kind,item_id)
);
-- 문서 목록표 rag.document_catalog(색인된 실행마다 한 줄: 이름·쪽수·검색 항목 수·현재 검색 대상 여부)는
-- rag/catalog.py가 정의하고 채운다(색인 트랜잭션과 첫 조회 때 만든다).
INSERT INTO staging.schema_revision(revision) VALUES ('027-document-rag-v1') ON CONFLICT DO NOTHING;
