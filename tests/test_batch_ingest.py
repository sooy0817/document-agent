"""작업 큐와 일괄 분석 작업자: 중복 방지, 한 작업은 한 작업자만, 실패 분류, 죽은 작업자 회수."""
import json
import os
import sys
import threading
import uuid

import fitz
import pytest

from doc_understanding.services import batch_ingest as bi

LOCAL_DB = pytest.mark.skipif(os.environ.get("NH_TEST_LOCAL_POSTGRES") != "1", reason="opt-in local PostgreSQL")


def pdf(path, text="hello", pages=1, password=None):
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page().insert_text((72, 72), text)
    options = dict(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password) if password else {}
    doc.save(path, **options)
    return path


class MemoryQueue:
    """job_queue와 같은 함수 이름을 가진 메모리 큐. 작업자 흐름만 검사한다(SQL은 통합 테스트가 검사)."""

    def __init__(self, items, max_attempts=2):
        self.lock = threading.Lock()
        self.jobs = [dict(job_id=n, queue="q", status="queued", attempts=0, max_attempts=max_attempts,
                          runs=0, errors=[], result=None, worker=None, **{"stage": bi.STAGE, "payload": {}, **item}) for n, item in enumerate(items, 1)]

    def reclaim(self, queue, stale):
        return 0

    def claim(self, queue, stage, worker):
        with self.lock:
            for job in self.jobs:
                if job["status"] == "queued" and job["stage"] == stage:
                    job.update(status="running", worker=worker, attempts=job["attempts"] + 1, runs=job["runs"] + 1)
                    return dict(job)
        return None

    def _mine(self, job):
        row = self.jobs[job["job_id"] - 1]
        mine = row["status"] == "running" and (row["worker"], row["attempts"]) == (job["worker"], job["attempts"])
        return row if mine else None

    def heartbeat(self, job):
        return self._mine(job) is not None

    def complete(self, job, result, *, next_stage=None, next_payload=None):
        with self.lock:
            row = self._mine(job)
            if row:
                row.update(status="done", result=result)
                if next_stage:
                    self.jobs.append(dict(row, job_id=len(self.jobs) + 1, stage=next_stage, status="queued", attempts=0,
                                          runs=0, errors=[], result=None, payload=next_payload, worker=None))
            return bool(row)

    def fail(self, job, kind, detail="", *, retry=True, delay_seconds=0):
        with self.lock:
            row = self._mine(job)
            state = "queued" if retry and row["attempts"] < row["max_attempts"] else "quarantined"
            row["errors"].append(dict(kind=kind, detail=detail))
            row.update(status=state, error_kind=kind)
            return state

    def release(self, job):
        with self.lock:
            row = self._mine(job)
            row.update(status="queued", attempts=row["attempts"] - 1)

    def pending(self, queue, stages):
        stages = [stages] if isinstance(stages, str) else stages
        return sum(j["status"] in ("queued", "running") and j["stage"] in stages for j in self.jobs)

    def requeue(self):
        for job in self.jobs:
            if job["status"] == "quarantined":
                job.update(status="queued", attempts=0)


def script(body):
    """분석 명령 대신 실행할 짧은 프로그램. 인자는 (원본, 출력 폴더)."""
    return lambda job, out, embedding=None: [sys.executable, "-c", "import sys, json, time, pathlib\n" + body,
                                             str(job["source_path"]), str(out)]


OK = ("out = pathlib.Path(sys.argv[2])\n"
      "(out / 'result.json').write_text(json.dumps(dict(tables=[dict(state='partial', cells=[dict(state='observed'), "
      "dict(state='unresolved')])], pages=[dict(text_status='extracted')], processing_status='COMPLETED')))\n")
OPTIONS = dict(base_seconds=30, seconds_per_page=1, stale_seconds=60, retry_delay_seconds=0, poll_seconds=0.05)


def run(queue, tmp_path, command, workers=2, **overrides):
    logs = []
    bi.work("q", workers, queue=queue, log=logs.append, output=tmp_path / "out", command=command,
            **{**OPTIONS, **overrides})
    return logs


def test_scan_skips_files_with_the_same_content(tmp_path):
    first = pdf(tmp_path / "a.pdf")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "copy.PDF").write_bytes(first.read_bytes())
    pdf(tmp_path / "b.pdf", "other")
    (tmp_path / "note.txt").write_text("x")
    items, duplicates = bi.scan([tmp_path])
    assert len(items) == 2 and duplicates == 1 and len({i["document_sha256"] for i in items}) == 2


def test_inspect_names_the_problem_before_a_process_is_started(tmp_path):
    good = pdf(tmp_path / "good.pdf", pages=3)
    digest = bi.scan([good])[0][0]["document_sha256"]
    assert bi.inspect(good, digest)[0] == 3

    def kind(path, sha=None):
        with pytest.raises(bi.JobError) as error:
            bi.inspect(path, sha or bi.scan([path])[0][0]["document_sha256"])
        return error.value.kind

    (tmp_path / "text.pdf").write_text("not a pdf")
    (tmp_path / "broken.pdf").write_bytes(b"%PDF-1.7\nbroken")
    assert kind(tmp_path / "text.pdf") == "NOT_PDF"
    assert kind(tmp_path / "broken.pdf") == "UNREADABLE"
    assert kind(pdf(tmp_path / "locked.pdf", password="pw")) == "ENCRYPTED"
    assert kind(good, "0" * 64) == "SOURCE_CHANGED"
    assert kind(tmp_path / "gone.pdf", digest) == "SOURCE_MISSING"


def test_workers_finish_every_document_once_and_record_size_and_time(tmp_path):
    items, _ = bi.scan([pdf(tmp_path / f"{n}.pdf", f"doc {n}", pages=n + 1) for n in range(5)])
    queue = MemoryQueue(items)
    run(queue, tmp_path, script(OK), workers=3)
    assert [j["status"] for j in queue.jobs] == ["done"] * 5 and [j["attempts"] for j in queue.jobs] == [1] * 5
    result = queue.jobs[2]["result"]
    assert (result["pages"], result["tables"], result["cells"], result["table_states"]) == (3, 1, 2, {"partial": 1})
    assert result["seconds"] > 0 and result["page_text_status"] == {"extracted": 1}
    summary = bi.report([dict(j, started_at=None, finished_at=None) for j in queue.jobs])
    assert summary["documents"] == 5 and summary["statuses"] == {"done": 5} and summary["pages"] == 15
    assert summary["table_states"] == {"partial": 5} and summary["page_text_status"] == {"extracted": 5}


def test_a_failing_document_is_retried_then_quarantined_and_the_rest_continue(tmp_path):
    items, _ = bi.scan([pdf(tmp_path / "bad.pdf", "bad"), pdf(tmp_path / "good.pdf", "good")])
    bad = next(i for i in items if i["source_path"].endswith("bad.pdf"))["document_sha256"]
    queue = MemoryQueue(items)
    run(queue, tmp_path, script("if 'bad' in sys.argv[1]: raise ValueError('secret text from the document')\n" + OK))
    by_sha = {j["document_sha256"]: j for j in queue.jobs}
    failed = by_sha.pop(bad)
    assert failed["status"] == "quarantined" and failed["attempts"] == 2
    assert [e["kind"] for e in failed["errors"]] == ["ANALYSIS_FAILED"] * 2
    assert failed["errors"][0]["detail"] == "ValueError"  # 예외 종류만 남기고 메시지는 남기지 않는다
    assert [j["status"] for j in by_sha.values()] == ["done"]
    summary = bi.report([dict(j, started_at=None, finished_at=None) for j in queue.jobs])
    assert summary["statuses"] == {"quarantined": 1, "done": 1} and summary["error_attempts_by_kind"] == {"ANALYSIS_FAILED": 2}


def test_a_document_over_its_time_limit_is_stopped_and_quarantined_without_retry(tmp_path):
    items, _ = bi.scan([pdf(tmp_path / "slow.pdf")])
    queue = MemoryQueue(items)
    run(queue, tmp_path, script("time.sleep(30)\n"), workers=1, base_seconds=0.3, seconds_per_page=0)
    assert queue.jobs[0]["status"] == "quarantined" and queue.jobs[0]["attempts"] == 1
    assert queue.jobs[0]["errors"][0]["kind"] == "TIMEOUT"


def test_unreadable_files_are_quarantined_without_starting_a_process(tmp_path):
    (tmp_path / "broken.pdf").write_bytes(b"%PDF-1.7\nbroken")
    items, _ = bi.scan([tmp_path / "broken.pdf", pdf(tmp_path / "locked.pdf", password="pw")])
    queue = MemoryQueue(items)
    started = []
    run(queue, tmp_path, lambda source, out: started.append(source) or script(OK)(source, out))
    assert sorted(j["error_kind"] for j in queue.jobs) == ["ENCRYPTED", "UNREADABLE"] and not started
    assert [j["attempts"] for j in queue.jobs] == [1, 1]


def test_a_worker_that_lost_ownership_stops_and_does_not_write_a_result(tmp_path):
    items, _ = bi.scan([pdf(tmp_path / "a.pdf")])
    queue = MemoryQueue(items)
    job = queue.claim("q", bi.STAGE, "w1")
    queue.jobs[0].update(worker="w2", attempts=2)  # 생존 신호가 끊겨 다른 작업자에게 넘어간 상태
    result = bi.run_job(job, queue, output=tmp_path / "out", base_seconds=30, seconds_per_page=0,
                        command=script("time.sleep(30)\n"), heartbeat_seconds=0.1, poll_seconds=0.05)
    assert result is None and not queue.complete(job, {}) and queue.jobs[0]["status"] == "running"


def test_a_stop_request_returns_the_job_to_the_queue_without_using_an_attempt(tmp_path):
    items, _ = bi.scan([pdf(tmp_path / "a.pdf")])
    queue = MemoryQueue(items)
    stop = threading.Event()
    thread = threading.Thread(target=bi.worker_loop, args=("w", "q", queue, stop, lambda _: None), kwargs=dict(
        OPTIONS, output=tmp_path / "out", command=script("time.sleep(30)\n")))
    thread.start()
    while queue.jobs[0]["status"] != "running":
        pass
    stop.set()
    thread.join(10)
    assert not thread.is_alive() and queue.jobs[0]["status"] == "queued" and queue.jobs[0]["attempts"] == 0


def test_resuming_after_a_stop_uses_a_new_folder_and_keeps_the_stopped_run(tmp_path):
    items, _ = bi.scan([pdf(tmp_path / "a.pdf")])
    queue = MemoryQueue(items)
    stop = threading.Event()
    slow = script("(pathlib.Path(sys.argv[2]) / 'partial.txt').write_text('first run')\n"
                  "sys.stderr.write('first log'); sys.stderr.flush(); time.sleep(30)\n")
    first = tmp_path / "out" / "q" / "1" / "r1"
    thread = threading.Thread(target=bi.worker_loop, args=("w", "q", queue, stop, lambda _: None), kwargs=dict(
        OPTIONS, output=tmp_path / "out", command=slow))
    thread.start()
    while not (first / "partial.txt").is_file() or not (first / "worker-stderr.txt").read_text():
        pass
    stop.set()
    thread.join(10)
    assert queue.jobs[0]["status"] == "queued" and queue.jobs[0]["attempts"] == 0
    run(queue, tmp_path, script(OK))
    job = queue.jobs[0]
    assert job["status"] == "done" and job["attempts"] == 1 and job["result"]["output"].endswith("r2")
    assert (first / "partial.txt").read_text() == "first run" and (first / "worker-stderr.txt").read_text() == "first log"
    assert sorted(f.name for f in (tmp_path / "out" / "q" / "1").iterdir()) == ["r1", "r2"]
    assert not (tmp_path / "out" / "q" / "1" / "r2" / "partial.txt").exists()


def test_retrying_a_quarantined_job_uses_a_new_folder_and_keeps_the_failed_runs(tmp_path):
    items, _ = bi.scan([pdf(tmp_path / "a.pdf")])
    queue = MemoryQueue(items)
    run(queue, tmp_path, script("raise ValueError('boom')\n"), workers=1)
    assert queue.jobs[0]["status"] == "quarantined" and queue.jobs[0]["attempts"] == 2
    queue.requeue()
    run(queue, tmp_path, script(OK), workers=1)
    job, folder = queue.jobs[0], tmp_path / "out" / "q" / "1"
    assert job["status"] == "done" and job["attempts"] == 1 and job["result"]["output"].endswith("r3")
    assert sorted(f.name for f in folder.iterdir()) == ["r1", "r2", "r3"]
    assert all("ValueError" in (folder / r / "worker-stderr.txt").read_text() for r in ("r1", "r2"))
    assert (folder / "r3" / "worker-stderr.txt").read_text() == ""


class BrokenQueue(MemoryQueue):
    def claim(self, queue, stage, worker):
        raise ConnectionError("db down: secret text")


def test_work_fails_when_workers_stop_on_a_queue_error(tmp_path):
    items, _ = bi.scan([pdf(tmp_path / "a.pdf")])
    logs = []
    with pytest.raises(bi.WorkFailed) as error:
        bi.work("q", 2, queue=BrokenQueue(items), log=logs.append, output=tmp_path / "out", **OPTIONS)
    assert "2 of 2 workers stopped: ConnectionError" in str(error.value) and "secret" not in str(error.value)
    assert len(logs) == 2 and all(line.endswith("stopped: ConnectionError") for line in logs)


def test_work_fails_when_one_worker_stops_even_if_the_others_finish(tmp_path):
    items, _ = bi.scan([pdf(tmp_path / "a.pdf"), pdf(tmp_path / "b.pdf", "b")])

    class Flaky(MemoryQueue):
        calls = 0

        def reclaim(self, queue, stale):
            with self.lock:
                Flaky.calls += 1
                if Flaky.calls == 1:
                    raise ConnectionError
            return 0

    queue = Flaky(items)
    with pytest.raises(bi.WorkFailed, match="1 of 2 workers stopped"):
        run(queue, tmp_path, script(OK))
    assert [j["status"] for j in queue.jobs] == ["done", "done"]


def test_the_work_command_exits_nonzero_when_the_queue_cannot_be_read(tmp_path, monkeypatch, capsys):
    from doc_understanding.runtime import job_queue

    def broken(*args, **options):
        raise ConnectionError

    monkeypatch.setattr(job_queue, "migrate", lambda: None)
    monkeypatch.setattr(job_queue, "reclaim", broken)
    assert bi.main(["work", "--queue", "q", "--workers", "2", "--output", str(tmp_path / "out")]) == 1
    captured = capsys.readouterr()
    assert "work failed: 2 of 2 workers stopped: ConnectionError" in captured.err and '"wall_seconds"' not in captured.out


STAGED = ("out = pathlib.Path(sys.argv[2])\n"
          "(out / 'stage-result.json').write_text(json.dumps(dict(run_id='run-1', search_entries=7)))\n")


def staged(fail=()):
    """단계마다 다른 가짜 프로그램: 분석은 result.json, 색인·임베딩은 stage-result.json을 쓴다."""
    def command(job, out, embedding=None):
        body = "raise ValueError('boom')\n" if job["stage"] in fail else OK if job["stage"] == "analyze" else STAGED
        return script(body)(job, out)
    return command


def test_a_document_moves_through_every_stage_up_to_the_one_named_at_enqueue(tmp_path):
    items, _ = bi.scan([pdf(tmp_path / "a.pdf", pages=3), pdf(tmp_path / "b.pdf", "b"), pdf(tmp_path / "c.pdf", "c")])
    through = ["embed", "index", None]
    queue = MemoryQueue([dict(i, payload=dict(through=t) if t else {}) for i, t in zip(items, through)])
    bi.work("q", dict(analyze=2, index=1, embed=1), queue=queue, log=lambda _: None, output=tmp_path / "out",
            command=staged(), **OPTIONS)
    by_stage = {stage: [j for j in queue.jobs if j["stage"] == stage] for stage in bi.STAGES}
    assert [len(by_stage[stage]) for stage in bi.STAGES] == [3, 2, 1]
    assert all(j["status"] == "done" for j in queue.jobs)
    first = next(j for j in by_stage["index"] if j["document_sha256"] == items[0]["document_sha256"])
    analyzed = queue.jobs[0]["result"]
    # 다음 단계는 앞 단계 결과를 입력으로 받고, 쪽 수는 분석이 잰 값을 이어 쓴다.
    assert first["payload"] == dict(through="embed", input=analyzed) and first["result"]["pages"] == 3
    assert by_stage["embed"][0]["payload"]["input"]["run_id"] == "run-1"
    assert by_stage["embed"][0]["result"]["search_entries"] == 7


def test_a_failed_stage_is_named_and_does_not_start_the_next_stage(tmp_path):
    items, _ = bi.scan([pdf(tmp_path / "a.pdf")])
    queue = MemoryQueue([dict(items[0], payload=dict(through="embed"))])
    bi.work("q", dict(analyze=1, index=1, embed=1), queue=queue, log=lambda _: None, output=tmp_path / "out",
            command=staged(fail=("index",)), **OPTIONS)
    assert [(j["stage"], j["status"], j.get("error_kind")) for j in queue.jobs] == [
        ("analyze", "done", None), ("index", "quarantined", "INDEX_FAILED")]


def test_index_and_embed_time_limits_grow_with_cells_and_entries():
    def limit(stage, **previous):
        return bi.stage_time_limit(dict(stage=stage, payload=dict(input=previous)), 2, 100, 10, 60, 2)

    assert bi.stage_time_limit(dict(stage="analyze", payload={}), 2, 100, 10, 60, 2) == 120  # 분석은 쪽 수만
    assert limit("index", cells=10000) == 720 and limit("index", cells=0) == 120
    assert limit("embed", search_entries=3000, cells=10000) == 6120 and limit("embed") == 120


def test_a_job_with_many_entries_is_not_stopped_by_the_page_based_limit(tmp_path):
    items, _ = bi.scan([pdf(tmp_path / "a.pdf")])
    job = dict(job_id=1, queue="q", stage="embed", runs=1, attempts=1, max_attempts=1, worker="w",
               payload=dict(input=dict(pages=1, bytes=1, run_id="r", search_entries=100)), **items[0])
    slow = lambda job, out, embedding=None: script("time.sleep(1.5)\n" + STAGED)(job, out)  # noqa: E731

    class Alive:
        heartbeat = staticmethod(lambda job: True)

    options = dict(output=tmp_path / "o", base_seconds=0.5, seconds_per_page=0, command=slow, poll_seconds=0.05)
    with pytest.raises(bi.JobError) as error:
        bi.run_job(job, Alive, seconds_per_entry=0, **options)
    assert error.value.kind == "TIMEOUT"
    result = bi.run_job(dict(job, runs=2), Alive, seconds_per_entry=2, **options)
    assert result["time_limit_seconds"] == 200.5 and result["search_entries"] == 7


def test_not_done_report_lists_every_stage_with_what_to_do(tmp_path):
    def row(job_id, status, kind=None, detail="", name="a.pdf", attempts=1, runs=1):
        return dict(job_id=job_id, status=status, error_kind=kind, attempts=attempts, runs=runs,
                    source_path=str(tmp_path / name), errors=[dict(kind=kind, detail=detail)] if kind else [])

    table = bi.not_done_table(dict(
        analyze=[row(1, "done"), row(2, "quarantined", "NOT_PDF", name="가짜.pdf"),
                 row(3, "queued", "ANALYSIS_FAILED", "ValueError", attempts=1)],
        embed=[row(9, "quarantined", "EMBED_FAILED", "RuntimeError", name="큰표.pdf", attempts=2, runs=3)]))
    assert [(r["stage"], r["file"], r["status"], r["folder"]) for r in table] == [
        ("analyze", "가짜.pdf", "quarantined", "2/r1"), ("analyze", "a.pdf", "queued", "3/r1"),
        ("embed", "큰표.pdf", "quarantined", "9/r3")]
    assert "PDF가 아니다" in table[0]["advice"] and table[1]["advice"] == "" and "embedding-audit.json" in table[2]["advice"]
    assert table[2]["last_detail"] == "RuntimeError"
    bi.write_not_done(tmp_path / "not-done.csv", table)
    text = (tmp_path / "not-done.csv").read_bytes()
    assert text.startswith(b"\xef\xbb\xbf") and "큰표.pdf" in text.decode("utf-8-sig") and text.decode("utf-8-sig").count("\r\n") == 4
    assert set(bi.FAILED.values()) | bi.PERMANENT | {"RESULT_UNREADABLE", "WORKER_ERROR", "WORKER_LOST"} == set(bi.ADVICE)


def test_stage_commands_pass_the_previous_result_to_the_next_process(tmp_path):
    job = dict(stage="index", source_path=str(tmp_path / "보고서.pdf"), payload=dict(input=dict(output="D:/a/r1")))
    assert bi.stage_command(job, tmp_path, {})[-4:] == ["_index", "D:/a/r1", str(tmp_path), "보고서.pdf"]
    job = dict(stage="embed", source_path="x.pdf", payload=dict(input=dict(run_id="run-9")))
    command = bi.stage_command(job, tmp_path, bi.embedding_settings())
    assert command[-4:-1] == ["_embed", "run-9", str(tmp_path)] and json.loads(command[-1]) == bi.LOCAL_EMBEDDING


@pytest.fixture(autouse=True)
def no_installed_embedding_choice(monkeypatch, tmp_path):
    # 개발 PC에 설치 설정 파일이 있어도 테스트는 영향을 받지 않는다.
    monkeypatch.setenv("NH_EMBEDDING_CONFIG", str(tmp_path / "no-embedding.json"))


def test_the_embedding_model_is_whatever_the_user_configured(tmp_path, monkeypatch):
    from doc_understanding.rag.embedding.models import EmbeddingConfig

    assert bi.embedding_settings() == bi.LOCAL_EMBEDDING  # 정하지 않으면 이 컴퓨터의 Ollama
    chosen = tmp_path / "embedding.json"
    monkeypatch.setenv("NH_EMBEDDING_CONFIG", str(chosen))
    chosen.write_text(json.dumps(dict(provider="compatible", base_url="http://127.0.0.1:9000/v1", model="any-model",
                                      dimensions=384, input_usd_per_million=0, timeout_seconds=60)))
    config = EmbeddingConfig(**bi.embedding_settings())
    assert (config.provider, config.model, config.dimensions, config.endpoint, config.external) == (
        "compatible", "any-model", 384, "http://127.0.0.1:9000/v1", False)
    # 명령 인자가 파일보다 먼저다.
    assert bi.embedding_settings(model="other")["model"] == "other" and bi.embedding_settings("local") == bi.LOCAL_EMBEDDING
    # 이 컴퓨터가 아닌 서버는 어떤 모델이든 외부 전송 동의가 있어야 한다.
    chosen.write_text(json.dumps(dict(provider="compatible", base_url="http://10.0.0.5:8000/v1", model="m",
                                      dimensions=8, input_usd_per_million=0, api_key_env="INTERNAL_KEY")))
    with pytest.raises(ValueError, match="EXTERNAL_EMBEDDING_NEEDS_ALLOW_EXTERNAL"):
        bi.embedding_settings()
    assert EmbeddingConfig(**bi.embedding_settings(allow_external=True)).key_env == "INTERNAL_KEY"
    # none: 임베딩 없이 키워드 검색만. 임베딩 작업자를 띄우는 명령은 시작하지 않는다.
    chosen.write_text(json.dumps(dict(provider="none")))
    assert bi.embedding_settings() is None
    with pytest.raises(SystemExit):
        bi.main(["work", "--queue", "q", "--embed-workers", "1"])
    with pytest.raises(ValueError):
        bi.embedding_settings("compatible", model="m", dimensions=8)  # 주소 없음


def test_reviewed_documents_are_queued_for_storing_without_another_review(tmp_path):
    manifest, document = tmp_path / "staging-manifest.json", tmp_path / "document.json"
    manifest.write_text("{}")
    document.write_text("{}")
    entry = dict(document_id="d1", source_path=str(tmp_path / "원본.pdf"), source_sha256="a" * 64, pages=7, cells=900,
                 manifest_path=str(manifest), document_path=str(document), previous_run_id="old-run")
    item = bi.reviewed_items([entry], "embed")[0]
    assert item["document_sha256"] == "a" * 64 and item["payload"]["through"] == "embed"
    previous = item["payload"]["input"]
    assert (previous["pages"], previous["cells"], previous["bytes"]) == (7, 900, 2)
    job = dict(stage="index", source_path=entry["source_path"], payload=item["payload"])
    # 저장만 하는 명령으로 간다(검토를 다시 하는 _index가 아니다). 표시 이름은 예전 실행에서 옮긴다.
    assert bi.stage_command(job, tmp_path, {})[-6:] == [
        "_store", str(manifest.resolve()), str(document.resolve()), str(tmp_path), "", "old-run"]
    assert bi.stage_time_limit(job, previous["pages"], 100, 10, 60, 2) == 100 + 70 + 54
    with pytest.raises(ValueError, match="REVIEWED_DOCUMENT_FILES_MISSING: 1"):
        bi.reviewed_items([dict(entry, document_path=str(tmp_path / "none.json"))], "index")
    # 큐를 거치면 색인 → 임베딩으로 이어진다.
    queue = MemoryQueue([dict(item, stage="index")])
    bi.work("q", dict(index=1, embed=1), queue=queue, log=lambda _: None, output=tmp_path / "out", command=staged(),
            **OPTIONS)
    assert [(j["stage"], j["status"]) for j in queue.jobs] == [("index", "done"), ("embed", "done")]
    assert queue.jobs[0]["result"]["pages"] == 7


def test_external_embedding_needs_explicit_consent_and_a_price():
    from doc_understanding.rag.embedding.models import EmbeddingConfig

    assert EmbeddingConfig(**bi.embedding_settings()).external is False
    for options in (dict(), dict(allow_external=True), dict(usd_per_million=0.02)):
        with pytest.raises(ValueError, match="EXTERNAL_EMBEDDING_NEEDS"):
            bi.embedding_settings("openai", **options)
    config = EmbeddingConfig(**bi.embedding_settings("openai", allow_external=True, usd_per_million=0.02, max_cost_usd=0.5))
    assert config.external and (config.model, config.dimensions, config.max_cost_usd) == ("text-embedding-3-small", 1536, 0.5)
    with pytest.raises(SystemExit):
        bi.main(["work", "--queue", "q", "--embed-workers", "1", "--embedding-provider", "openai"])


@pytest.fixture
def db_queue():
    from doc_understanding.runtime import job_queue

    job_queue.migrate()
    name = "test-" + uuid.uuid4().hex[:8]
    yield job_queue, name
    job_queue.delete_queue(name)


def items_for(n):
    return [dict(document_sha256=f"{i:064d}", source_path=f"/x/{i}.pdf") for i in range(n)]


@pytest.mark.integration
@LOCAL_DB
def test_database_queue_does_not_duplicate_and_gives_each_job_to_one_worker(db_queue):
    queue, name = db_queue
    assert len(queue.enqueue(name, "analyze", items_for(20))) == 20
    assert queue.enqueue(name, "analyze", items_for(25)) == [f"{i:064d}" for i in range(20, 25)]
    claimed, lock = [], threading.Lock()

    def take(worker):
        while job := queue.claim(name, "analyze", worker):
            with lock:
                claimed.append(job["job_id"])

    threads = [threading.Thread(target=take, args=(f"w{n}",)) for n in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(claimed) == 25 == len(set(claimed)) and queue.pending(name, "analyze") == 25


@pytest.mark.integration
@LOCAL_DB
def test_database_queue_retry_quarantine_requeue_and_counts(db_queue):
    queue, name = db_queue
    queue.enqueue(name, "analyze", items_for(2))
    first = queue.claim(name, "analyze", "w")
    assert queue.fail(first, "ANALYSIS_FAILED", "ValueError", delay_seconds=0) == "queued"
    second = queue.claim(name, "analyze", "w")  # 먼저 들어온 줄이 다시 나온다
    assert second["job_id"] == first["job_id"] and second["attempts"] == 2
    assert queue.fail(second, "ANALYSIS_FAILED", "ValueError", delay_seconds=0) == "quarantined"
    other = queue.claim(name, "analyze", "w")
    assert queue.fail(other, "TIMEOUT", retry=False) == "quarantined" and other["attempts"] == 1
    assert queue.claim(name, "analyze", "w") is None and queue.pending(name, "analyze") == 0
    assert {(c["status"], c["error_kind"]): c["jobs"] for c in queue.counts(name)} == {
        ("quarantined", "ANALYSIS_FAILED"): 1, ("quarantined", "TIMEOUT"): 1}
    row = next(r for r in queue.rows(name, "analyze") if r["job_id"] == first["job_id"])
    assert [e["attempt"] for e in row["errors"]] == [1, 2]
    assert queue.requeue(name, "analyze", kinds=["TIMEOUT"]) == 1 and queue.requeue(name, "analyze") == 1
    again = queue.claim(name, "analyze", "w")
    assert again["attempts"] == 1 and queue.complete(again, dict(pages=1)) and not queue.complete(again, {})


@pytest.mark.integration
@LOCAL_DB
def test_database_queue_reclaims_a_dead_worker_and_ignores_its_late_result(db_queue):
    queue, name = db_queue
    queue.enqueue(name, "analyze", items_for(1), max_attempts=2)
    dead = queue.claim(name, "analyze", "dead")
    assert queue.reclaim(name, 3600) == 0 and queue.heartbeat(dead)
    assert queue.reclaim(name, 0) == 1 and not queue.heartbeat(dead)
    assert not queue.complete(dead, dict(late=True)) and queue.fail(dead, "X") is None
    alive = queue.claim(name, "analyze", "alive")
    assert alive["attempts"] == 2 and queue.release(alive)
    back = queue.claim(name, "analyze", "alive")
    assert back["attempts"] == 2 and queue.reclaim(name, 0) == 1  # 시도를 다 쓰면 격리
    row = queue.rows(name, "analyze")[0]
    assert row["status"] == "quarantined" and row["error_kind"] == "WORKER_LOST"


@pytest.mark.integration
@LOCAL_DB
def test_real_analysis_through_the_database_queue(db_queue, tmp_path):
    queue, name = db_queue
    (tmp_path / "broken.pdf").write_bytes(b"%PDF-1.7\nbroken")
    items, _ = bi.scan([pdf(tmp_path / "a.pdf", "first"), pdf(tmp_path / "b.pdf", "second", pages=2), tmp_path / "broken.pdf"])
    queue.enqueue(name, bi.STAGE, items)
    bi.work(name, 2, queue=queue, log=lambda _: None, output=tmp_path / "out", base_seconds=120, seconds_per_page=20,
            stale_seconds=120, retry_delay_seconds=0)
    rows = queue.rows(name, bi.STAGE)
    assert sorted(r["status"] for r in rows) == ["done", "done", "quarantined"]
    done = [r for r in rows if r["status"] == "done"]
    assert sorted(r["result"]["pages"] for r in done) == [1, 2]
    for row in done:
        out = row["result"]["output"]
        assert json.loads(open(os.path.join(out, "document.json"), encoding="utf8").read())["document_id"]
        assert row["result"]["peak_rss_mb"] > 20  # 분석 프로세스 자신의 값(가상 환경 실행 파일의 값이 아님)
    summary = bi.report(rows)
    assert summary["pages"] == 3 and summary["error_attempts_by_kind"] == {"UNREADABLE": 1}


@pytest.mark.integration
@LOCAL_DB
def test_database_queue_counts_every_claim_so_each_run_gets_its_own_folder(db_queue):
    queue, name = db_queue
    queue.enqueue(name, "analyze", items_for(1), max_attempts=1)
    first = queue.claim(name, "analyze", "w")
    assert (first["attempts"], first["runs"]) == (1, 1) and queue.release(first)
    resumed = queue.claim(name, "analyze", "w")  # 멈춘 뒤 재개: 시도는 그대로, 실행 번호는 새로
    assert (resumed["attempts"], resumed["runs"]) == (1, 2)
    assert queue.fail(resumed, "ANALYSIS_FAILED", "ValueError", delay_seconds=0) == "quarantined"
    assert queue.requeue(name, "analyze") == 1
    retried = queue.claim(name, "analyze", "w")  # 격리 후 다시 넣기: 시도는 1부터, 실행 번호는 이어서
    assert (retried["attempts"], retried["runs"]) == (1, 3)


@pytest.mark.integration
@LOCAL_DB
def test_database_queue_adds_the_next_stage_only_with_the_owners_completion(db_queue):
    queue, name = db_queue
    queue.enqueue(name, "analyze", items_for(2), max_attempts=3)
    job = queue.claim(name, "analyze", "w")
    late = dict(job, worker="someone-else")
    assert not queue.complete(late, dict(pages=1), next_stage="index", next_payload=dict(input=1))
    assert queue.pending(name, "index") == 0 and queue.pending(name, ["analyze", "index"]) == 2
    assert queue.complete(job, dict(pages=1), next_stage="index", next_payload=dict(through="index", input=dict(n=1)))
    index = queue.claim(name, "index", "w")
    assert index["document_sha256"] == job["document_sha256"] and index["payload"]["input"] == dict(n=1)
    assert index["max_attempts"] == 3 and queue.complete(index, dict(run_id="r"))
    other = queue.claim(name, "analyze", "w")
    assert queue.complete(other, dict(pages=2)) and queue.pending(name, bi.STAGES) == 0  # 다음 단계 없이 끝


@pytest.mark.integration
@LOCAL_DB
def test_database_queue_requeues_a_finished_next_stage_when_the_earlier_stage_is_run_again(db_queue):
    queue, name = db_queue
    queue.enqueue(name, "analyze", items_for(1), max_attempts=1)
    first = queue.claim(name, "analyze", "w")
    assert queue.complete(first, dict(v=1), next_stage="index", next_payload=dict(input=dict(v=1)))
    assert queue.complete(queue.claim(name, "index", "w"), dict(run_id="old"))
    from doc_understanding.storage.staging import local
    local.sql_params("UPDATE jobs.job SET status='queued', attempts=0 WHERE queue=%s AND stage='analyze'", [name])
    again = queue.claim(name, "analyze", "w")
    assert queue.complete(again, dict(v=2), next_stage="index", next_payload=dict(input=dict(v=2)))
    index = queue.claim(name, "index", "w")
    assert index["payload"]["input"] == dict(v=2) and index["attempts"] == 1 and index["runs"] == 2


SCRATCH_DB = pytest.mark.skipif(os.environ.get("NH_TEST_SCRATCH_POSTGRES") != "1",
                                reason="writes a document into the search corpus: disposable database only")


@pytest.mark.integration
@SCRATCH_DB
def test_real_analysis_and_indexing_through_the_database_queue(db_queue, tmp_path):
    queue, name = db_queue
    from doc_understanding.rag import catalog

    source = pdf(tmp_path / "queue-indexed.pdf", "queue indexing check", pages=2)
    items, _ = bi.scan([source])
    queue.enqueue(name, "analyze", [dict(items[0], payload=dict(through="index"))])
    bi.work(name, dict(analyze=1, index=1), log=lambda _: None, output=tmp_path / "o", base_seconds=120,
            seconds_per_page=20, stale_seconds=120, retry_delay_seconds=0)
    row = queue.rows(name, "index")[0]
    result = row["result"]
    assert row["status"] == "done" and result["search_entries"] >= 1 and result["pages"] == 2
    assert result["structure_review"]["status"] in ("no_change", "applied") and result["peak_rss_mb"] > 20
    listed = catalog.lookup([result["run_id"]])[result["run_id"]]
    assert listed["is_current"] and listed["name"] == "queue-indexed.pdf"
    assert queue.rows(name, "embed") == []
