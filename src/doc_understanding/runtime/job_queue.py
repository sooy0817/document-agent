"""영속 작업 큐: PostgreSQL 표 하나(jobs.job)로 여러 작업자가 문서 작업을 나눠 가진다.

- 같은 큐·단계·문서(내용 해시)는 한 줄이다. 다시 넣어도 늘지 않는다.
- 작업자는 `FOR UPDATE SKIP LOCKED`로 대기 작업 하나를 집는다. 두 작업자가 같은 줄을 집지 않는다.
- 집은 작업은 (작업자, 시도 번호)가 소유한다. 생존 신호가 끊겨 되돌려진 뒤 늦게 끝난 작업자는 결과를 쓰지 못한다.
- 중복 실행 자체를 막지는 않는다(생존 신호가 끊겼지만 살아 있던 작업자). 작업은 다시 실행해도 안전해야 한다.
작업 내용은 모른다. 무엇을 실행할지는 부르는 쪽(services/batch_ingest.py)이 정한다.
"""
import json
from pathlib import Path

from doc_understanding.storage.staging import local

CLAIMED = "j.job_id, j.queue, j.stage, j.document_sha256, j.source_path, j.payload, j.attempts, j.max_attempts, j.runs"
OWNED = "job_id=%s AND worker=%s AND attempts=%s AND status='running'"


def migrate():
    local.sql("BEGIN;\n" + Path(__file__).with_name("job_queue.sql").read_text(encoding="utf-8") + "\nCOMMIT;")


def _rows(statement, params):
    return json.loads(local.sql_params(statement, params) or "[]")


def _changed(statement, params):
    """RETURNING 1 줄 수. psql은 상태 줄('UPDATE n')도 출력하므로 값 줄만 센다."""
    return sum(line == "1" for line in local.sql_params(statement, params).splitlines())


def enqueue(queue, stage, items, *, max_attempts=2, chunk=500):
    """items: document_sha256·source_path·(선택)payload. 새로 들어간 문서 해시 목록을 돌려준다."""
    added = []
    items = list(items)
    for start in range(0, len(items), chunk):
        part = [dict(document_sha256=i["document_sha256"], source_path=str(i["source_path"]),
                     payload=i.get("payload") or {}) for i in items[start:start + chunk]]
        added += _rows(
            "WITH c AS (INSERT INTO jobs.job (queue, stage, document_sha256, source_path, payload, max_attempts) "
            "SELECT %s, %s, x.document_sha256, x.source_path, x.payload, %s "
            "FROM jsonb_to_recordset(%s::jsonb) AS x(document_sha256 text, source_path text, payload jsonb) "
            "ON CONFLICT (queue, stage, document_sha256) DO NOTHING RETURNING document_sha256) "
            "SELECT coalesce(json_agg(c.document_sha256), '[]'::json)::text FROM c",
            [queue, stage, max_attempts, json.dumps(part, ensure_ascii=False)])
    return added


def claim(queue, stage, worker):
    """대기 작업 하나를 실행 중으로 바꿔 돌려준다. 없으면 None."""
    rows = _rows(
        "WITH picked AS (SELECT job_id FROM jobs.job WHERE queue=%s AND stage=%s AND status='queued' "
        "AND run_after <= now() ORDER BY job_id FOR UPDATE SKIP LOCKED LIMIT 1), "
        "c AS (UPDATE jobs.job j SET status='running', worker=%s, attempts=j.attempts+1, runs=j.runs+1, started_at=now(), "
        "heartbeat_at=now(), finished_at=NULL FROM picked WHERE j.job_id=picked.job_id "
        f"RETURNING {CLAIMED}) SELECT coalesce(json_agg(c), '[]'::json)::text FROM c",
        [queue, stage, worker])
    return dict(rows[0], worker=worker) if rows else None


def _owner(job):
    return [job["job_id"], job["worker"], job["attempts"]]


def heartbeat(job):
    """아직 이 작업의 주인이면 True. False면 다른 작업자에게 넘어갔으니 실행을 멈춘다."""
    return _changed(f"UPDATE jobs.job SET heartbeat_at=now() WHERE {OWNED} RETURNING 1", _owner(job)) == 1


def complete(job, result, *, next_stage=None, next_payload=None):
    """완료로 바꾼다. next_stage를 주면 같은 문장에서 다음 단계 작업을 대기로 넣는다(둘 다 되거나 둘 다 안 된다).

    다음 단계 줄이 이미 있으면(앞 단계를 다시 실행한 경우) 새 입력으로 다시 대기시킨다. 실행 중인 줄은 건드리지 않는다.
    """
    return _changed(
        "WITH done AS (UPDATE jobs.job SET status='done', finished_at=now(), error_kind=NULL, result=%s::jsonb "
        f"WHERE {OWNED} RETURNING queue, document_sha256, source_path, max_attempts), "
        "following AS (INSERT INTO jobs.job (queue, stage, document_sha256, source_path, payload, max_attempts) "
        "SELECT queue, %s, document_sha256, source_path, %s::jsonb, max_attempts FROM done WHERE %s::text IS NOT NULL "
        "ON CONFLICT (queue, stage, document_sha256) DO UPDATE SET payload=excluded.payload, status='queued', "
        "attempts=0, run_after=now(), worker=NULL, error_kind=NULL, result=NULL WHERE jobs.job.status <> 'running') "
        "SELECT 1 FROM done",
        [json.dumps(result, ensure_ascii=False)] + _owner(job)
        + [next_stage, json.dumps(next_payload or {}, ensure_ascii=False), next_stage]) == 1


def fail(job, kind, detail="", *, retry=True, delay_seconds=30):
    """실패를 기록한다. 다시 해 볼 만하고 시도가 남았으면 대기로, 아니면 격리로 옮긴다. 옮긴 상태를 돌려준다."""
    again = retry and job["attempts"] < job["max_attempts"]
    entry = json.dumps([dict(attempt=job["attempts"], kind=kind, detail=detail[:300])], ensure_ascii=False)
    done = _changed(
        "UPDATE jobs.job SET status=%s, finished_at=now(), error_kind=%s, errors=errors || %s::jsonb, "
        f"run_after=now() + make_interval(secs => %s) WHERE {OWNED} RETURNING 1",
        ["queued" if again else "quarantined", kind, entry, delay_seconds if again else 0] + _owner(job))
    return ("queued" if again else "quarantined") if done else None


def release(job):
    """작업자가 스스로 멈출 때: 시도 횟수를 쓰지 않고 대기로 돌려놓는다."""
    return _changed(
        f"UPDATE jobs.job SET status='queued', attempts=attempts-1, worker=NULL, heartbeat_at=NULL WHERE {OWNED} "
        "RETURNING 1", _owner(job)) == 1


def reclaim(queue, stale_seconds):
    """생존 신호가 끊긴 실행 중 작업을 되찾는다. 시도가 남았으면 대기로, 아니면 격리로. 옮긴 줄 수."""
    entry = json.dumps([dict(kind="WORKER_LOST", detail="")])
    return _changed(
        "UPDATE jobs.job SET status=CASE WHEN attempts < max_attempts THEN 'queued' ELSE 'quarantined' END, "
        "error_kind='WORKER_LOST', errors=errors || %s::jsonb, finished_at=now(), run_after=now() "
        "WHERE queue=%s AND status='running' AND heartbeat_at < now() - make_interval(secs => %s) RETURNING 1",
        [entry, queue, stale_seconds])


def requeue(queue, stage, *, kinds=None):
    """격리된 작업을 시도 횟수 0으로 다시 대기에 넣는다. kinds를 주면 그 오류 유형만."""
    kinds = list(kinds) if kinds else None
    return _changed(
        "UPDATE jobs.job SET status='queued', attempts=0, run_after=now(), worker=NULL "
        "WHERE queue=%s AND stage=%s AND status='quarantined' AND (%s::text[] IS NULL OR error_kind = ANY(%s::text[])) "
        "RETURNING 1", [queue, stage, kinds, kinds])


def counts(queue):
    """상태별·오류 유형별 줄 수."""
    return _rows(
        "WITH c AS (SELECT stage, status, error_kind, count(*) AS jobs FROM jobs.job WHERE queue=%s "
        "GROUP BY stage, status, error_kind ORDER BY stage, status, error_kind) "
        "SELECT coalesce(json_agg(c), '[]'::json)::text FROM c", [queue])


def pending(queue, stages):
    """아직 끝나지 않은(대기·실행 중) 줄 수. stages는 단계 이름 하나 또는 여러 개."""
    stages = [stages] if isinstance(stages, str) else list(stages)
    return int(local.sql_params(
        "SELECT count(*) FROM jobs.job WHERE queue=%s AND stage = ANY(%s::text[]) AND status IN ('queued', 'running')",
        [queue, stages]).strip().splitlines()[0])


def rows(queue, stage):
    return _rows(
        "WITH c AS (SELECT job_id, document_sha256, source_path, status, attempts, runs, worker, error_kind, errors, result, "
        "started_at, finished_at FROM jobs.job WHERE queue=%s AND stage=%s ORDER BY job_id) "
        "SELECT coalesce(json_agg(c), '[]'::json)::text FROM c", [queue, stage])


def delete_queue(queue):
    """큐의 모든 줄을 지운다. 측정·테스트용 큐를 치울 때만 쓴다."""
    return _changed("DELETE FROM jobs.job WHERE queue=%s RETURNING 1", [queue])
