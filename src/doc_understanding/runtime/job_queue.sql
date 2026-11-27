-- 영속 작업 큐. 문서 하나의 단계 하나가 한 줄이다. 원본·IR·검색 색인은 이 표에 넣지 않는다.
CREATE SCHEMA IF NOT EXISTS jobs;
CREATE TABLE IF NOT EXISTS jobs.job (
    job_id bigserial PRIMARY KEY,
    queue text NOT NULL,
    stage text NOT NULL,
    document_sha256 text NOT NULL,
    source_path text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'done', 'quarantined')),
    attempts integer NOT NULL DEFAULT 0,
    -- 집어 간 횟수. 멈춤·재등록으로 attempts가 줄어도 줄지 않아 실행마다 다른 산출물 폴더를 쓴다.
    runs integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 2 CHECK (max_attempts >= 1),
    run_after timestamptz NOT NULL DEFAULT now(),
    worker text,
    heartbeat_at timestamptz,
    started_at timestamptz,
    finished_at timestamptz,
    error_kind text,
    errors jsonb NOT NULL DEFAULT '[]'::jsonb,
    result jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (queue, stage, document_sha256)
);
ALTER TABLE jobs.job ADD COLUMN IF NOT EXISTS runs integer NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS job_claim ON jobs.job (queue, stage, job_id) WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS job_running ON jobs.job (heartbeat_at) WHERE status = 'running';
