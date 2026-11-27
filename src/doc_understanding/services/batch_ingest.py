"""폴더의 PDF를 작업 큐에 넣고 여러 작업자가 단계별로 처리한다: 분석 → 색인(규칙 구조 검토 포함) → 임베딩.

    python -m doc_understanding.services.batch_ingest enqueue --queue Q [--through embed] <폴더 또는 PDF> ...
    python -m doc_understanding.services.batch_ingest work    --queue Q --workers 4 [--index-workers 2 --embed-workers 1]
    python -m doc_understanding.services.batch_ingest enqueue-reviewed --queue Q --manifest 목록.json [--through embed]
    python -m doc_understanding.services.batch_ingest status  --queue Q
    python -m doc_understanding.services.batch_ingest report  --queue Q [--stage index] --output <새 폴더>
    python -m doc_understanding.services.batch_ingest retry   --queue Q [--stage index] [--kind TIMEOUT]

작업자는 작업마다 별도 프로세스를 띄워 기존 코드를 그대로 부른다(분석 `services.documents.worker`,
색인 `agents.structure_review.review_document` + `rag.repository.store`, 임베딩 `rag.embedding.indexer.embed_run`).
파서 코드와 결과 형식은 바꾸지 않는다. 구조 검토는 규칙만 쓰고 모델을 부르지 않는다. 임베딩 모델은 사용자가 고른다:
설치 설정 파일(`config/embedding.json`) 또는 `--embedding-*` 인자. 정하지 않으면 이 컴퓨터의 Ollama `bge-m3`다.
문서 글자가 이 컴퓨터 밖으로 나가는 설정은 `--allow-external`이 있어야 시작한다.
결과는 `<output>/<queue>/<작업 번호>/r<실행 번호>/`에 쓴다. 한 단계가 끝나면 등록할 때 정한 단계(`--through`)까지
다음 단계 작업이 자동으로 대기에 들어간다.
`work`는 스레드마다 프로세스 하나를 감독한다. 같은 큐에 `work`를 여러 번(다른 서버에서도) 띄울 수 있다.
"""
import argparse
import gc
import hashlib
import json
import os
import socket
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

STAGES = ("analyze", "index", "embed")
STAGE = STAGES[0]
MODULE = "doc_understanding.services.batch_ingest"
ROOT = Path(__file__).resolve().parents[3]
FAILED = dict(analyze="ANALYSIS_FAILED", index="INDEX_FAILED", embed="EMBED_FAILED")
# 다시 해도 같은 결과가 나올 실패. 재시도하지 않고 바로 격리한다.
PERMANENT = {"NOT_PDF", "ENCRYPTED", "UNREADABLE", "NO_PAGES", "SOURCE_MISSING", "SOURCE_CHANGED", "TIMEOUT"}


class JobError(Exception):
    def __init__(self, kind, detail=""):
        super().__init__(kind)
        self.kind, self.detail = kind, detail


def scan(paths):
    """PDF를 찾아 내용 해시로 중복을 걷어낸다. (문서 목록, 내용이 같아 건너뛴 파일 수)를 돌려준다."""
    files = []
    for path in map(Path, paths):
        files += sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf") if path.is_dir() else [path]
    seen, items = set(), []
    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest not in seen:
            seen.add(digest)
            items.append(dict(document_sha256=digest, source_path=str(path.resolve())))
    return items, len(files) - len(items)


def inspect(path, expected_sha256):
    """분석 프로세스를 띄우기 전에 걸러낼 수 있는 문제를 확인하고 쪽 수를 돌려준다."""
    import fitz

    path = Path(path)
    if not path.is_file():
        raise JobError("SOURCE_MISSING")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise JobError("SOURCE_CHANGED")
    if not data.startswith(b"%PDF-"):
        raise JobError("NOT_PDF")
    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            if doc.needs_pass:
                raise JobError("ENCRYPTED")
            pages = len(doc)
    except JobError:
        raise
    except Exception:
        raise JobError("UNREADABLE") from None
    if pages < 1:
        raise JobError("NO_PAGES")
    return pages, len(data)


def time_limit(pages, base_seconds, seconds_per_page):
    return base_seconds + seconds_per_page * pages


def stage_time_limit(job, pages, base_seconds, seconds_per_page, seconds_per_1000_cells, seconds_per_entry):
    """작업 하나의 제한 시간. 색인은 칸 수, 임베딩은 검색 항목 수를 더한다(앞 단계가 센 값).

    분석은 시작 전에 쪽 수만 알 수 있어 쪽 수로만 정한다. 색인·임베딩 시간은 쪽 수보다 칸·항목 수를 따르므로,
    쪽은 적은데 칸이 많은 문서가 시간 초과로 격리되지 않게 한다.
    """
    limit = time_limit(pages, base_seconds, seconds_per_page)
    previous = job["payload"].get("input") or {}
    if job["stage"] == "index":
        limit += seconds_per_1000_cells * (previous.get("cells") or 0) / 1000
    elif job["stage"] == "embed":
        limit += seconds_per_entry * (previous.get("search_entries") or 0)
    return round(limit, 1)


def stage_command(job, out, embedding):
    """단계별 작업 프로세스 명령. 앞 단계 결과는 payload["input"]에 있다."""
    head = [sys.executable, "-B", "-m", MODULE]
    if job["stage"] == "analyze":
        return head + ["_analyze", str(job["source_path"]), str(out)]
    previous = job["payload"]["input"]
    if job["stage"] == "index":
        if previous.get("document"):  # 검토가 끝난 문서를 받은 작업: 다시 검토하지 않고 저장만 한다.
            return head + ["_store", previous["manifest"], previous["document"], str(out), previous.get("name") or "",
                           previous.get("previous_run_id") or ""]
        return head + ["_index", previous["output"], str(out), Path(job["source_path"]).name]
    return head + ["_embed", previous["run_id"], str(out), json.dumps(embedding)]


def own_peak_rss_bytes():
    """이 프로세스의 최대 메모리. 못 읽으면 None."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                    (name, ctypes.c_size_t) for name in (
                        "peak_working_set", "working_set", "peak_paged_pool", "paged_pool",
                        "peak_nonpaged_pool", "nonpaged_pool", "pagefile", "peak_pagefile")]

            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            kernel = ctypes.WinDLL("kernel32")
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            read = ctypes.WinDLL("psapi").GetProcessMemoryInfo
            read.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            return counters.peak_working_set if read(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb) else None
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak if sys.platform == "darwin" else peak * 1024
    except Exception:
        return None


def analyze_document(source, out):
    from doc_understanding.services.documents import worker

    worker(Path(source), Path(out))


def index_document(analysis, out, name):
    """분석 결과를 규칙으로 구조 검토한 뒤 DB에 적재·색인한다. 웹의 검색 준비(모델 없이)와 같은 순서다."""
    from doc_understanding.agents.structure_review.review import review_document
    from doc_understanding.domain.pipeline_contracts import CanonicalDocument
    from doc_understanding.rag import catalog, repository

    analysis, out = Path(analysis), Path(out)
    source = analysis / "document.json"
    manifests = list((analysis / "extraction").glob("*/staging-manifest.json"))
    if len(manifests) != 1:
        raise ValueError("SOURCE_MANIFEST_REQUIRED")
    doc = CanonicalDocument.model_validate_json(source.read_bytes())
    try:  # 검토가 실패해도 원본 문서로 색인한다(웹과 같다). 원본 문서는 고치지 않는다.
        reviewed, found = review_document(doc)
        (out / "structure-review.json").write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf8")
        review = dict(status="applied" if reviewed is not None else "no_change", applied=len(found["applied"]),
                      rejected=len(found["rejected"]))
        if reviewed is not None:
            source = out / "document.json"
            source.write_text(reviewed.model_dump_json(indent=2), encoding="utf8")
    except Exception as error:
        review = dict(status="failed", error=type(error).__name__)
    # 적재는 문서를 파일에서 다시 읽는다. 여기서 읽은 사본을 먼저 놓아야 메모리에 같은 문서가 겹치지 않는다
    # (문서 JSON 150MB가 객체로는 약 1.3GB였다).
    doc = reviewed = found = None
    gc.collect()
    stored = repository.store(manifests[0], source, migrate_schema=False)
    catalog.set_names({stored["run_id"]: name})
    return dict(run_id=stored["run_id"], index_version=repository.INDEX_VERSION, search_entries=stored["search_entries"],
                indexed_document_sha256=stored["document_sha256"], structure_review=review)


LOCAL_EMBEDDING = dict(provider="local", model="bge-m3", dimensions=1024, input_usd_per_million=0.0, max_calls=1000,
                       batch_size=8, timeout_seconds=120.0, max_input_bytes=4500)


def embedding_settings(provider=None, *, model=None, dimensions=None, max_input_bytes=None, base_url=None,
                       api_key_env=None, allow_external=False, usd_per_million=None, max_cost_usd=None):
    """임베딩 설정(EmbeddingConfig 입력). 모델은 사용자가 고른다.

    provider를 주지 않으면 설치 설정 파일(`rag/embedding/settings.py`)을 쓰고, 파일도 없으면 이 컴퓨터의 Ollama다.
    `none`이면 None(임베딩하지 않음). 문서 글자가 이 컴퓨터 밖으로 나가는 설정은 allow_external이 있어야 한다.
    비용 한도는 문서 하나당이다.
    """
    from doc_understanding.rag.embedding import settings
    from doc_understanding.rag.embedding.models import EmbeddingConfig

    if provider is None:
        chosen = dict(settings.configured() or LOCAL_EMBEDDING)
    elif provider == "local":
        chosen = dict(LOCAL_EMBEDDING)
    elif provider == "openai":
        chosen = dict(provider="openai", model="text-embedding-3-small", dimensions=1536, max_calls=1000)
    else:
        chosen = dict(provider=provider, input_usd_per_million=0.0, max_calls=1000)
    chosen.update({k: v for k, v in dict(
        model=model, dimensions=dimensions, max_input_bytes=max_input_bytes, base_url=base_url, api_key_env=api_key_env,
        input_usd_per_million=usd_per_million, max_cost_usd=max_cost_usd).items() if v is not None})
    if chosen["provider"] == "none":
        return None
    if "input_usd_per_million" not in chosen:
        raise ValueError("EXTERNAL_EMBEDDING_NEEDS_PRICE")
    chosen = settings.normalized(chosen)
    if EmbeddingConfig(**chosen).external and not allow_external:
        raise ValueError("EXTERNAL_EMBEDDING_NEEDS_ALLOW_EXTERNAL")
    return chosen


def store_document(manifest, document, name, previous_run_id):
    """이미 구조 검토가 끝난 문서를 그대로 적재·색인한다. 검토를 다시 하지 않는다.

    같은 문서의 예전 실행(previous_run_id)이 있으면 그 표시 이름을 새 실행에 옮긴다. name을 주면 그것을 쓴다.
    """
    from doc_understanding.rag import catalog, repository
    from doc_understanding.storage.staging import local

    stored = repository.store(Path(manifest), Path(document), migrate_schema=False)
    run_id = stored["run_id"]
    if name:
        catalog.set_names({run_id: name})
    elif previous_run_id:
        local.sql_params(
            "UPDATE rag.document_catalog new SET display_name=old.display_name FROM rag.document_catalog old "
            "WHERE new.run_id=%s AND old.run_id=%s AND old.document_id=new.document_id "
            "AND new.display_name IS DISTINCT FROM old.display_name", [run_id, previous_run_id])
    return dict(run_id=run_id, index_version=repository.INDEX_VERSION, search_entries=stored["search_entries"],
                indexed_document_sha256=stored["document_sha256"], already_stored=bool(stored.get("duplicate")),
                structure_review=dict(status="provided"))


def reviewed_items(entries, through):
    """목록(검토가 끝난 문서)의 항목을 색인 단계 작업으로 바꾼다. 빠진 파일이 있으면 넣기 전에 알린다."""
    items, missing = [], []
    for entry in entries:
        manifest, document = Path(entry["manifest_path"]), Path(entry["document_path"])
        if not manifest.is_file() or not document.is_file():
            missing.append(entry.get("document_id") or str(document))
            continue
        items.append(dict(
            document_sha256=entry["source_sha256"], source_path=entry["source_path"],
            payload=dict(through=through, input=dict(
                manifest=str(manifest.resolve()), document=str(document.resolve()), name=entry.get("name"),
                previous_run_id=entry.get("previous_run_id"), pages=int(entry.get("pages") or 0),
                bytes=document.stat().st_size, cells=int(entry.get("cells") or 0)))))
    if missing:
        raise ValueError(f"REVIEWED_DOCUMENT_FILES_MISSING: {len(missing)} ({', '.join(missing[:3])})")
    return items


def embed_document(run_id, out, settings):
    """색인된 실행의 검색 항목을 임베딩한다. 이미 있는 벡터는 다시 만들지 않는다."""
    from doc_understanding.rag.embedding.client import EmbeddingClient
    from doc_understanding.rag.embedding.indexer import embed_run
    from doc_understanding.rag.embedding.models import EmbeddingConfig

    # max_input_bytes보다 긴 항목은 보내지 않고 skipped로 센다(키워드 검색에는 남는다). Ollama는 한 입력이
    # 약 2,048토큰을 넘으면 묶음 전체를 거부하므로 로컬 기본값은 웹 기본값(7,500바이트)보다 낮다.
    config = EmbeddingConfig(**json.loads(settings))
    client = EmbeddingClient(config, allow_external=True)
    try:
        done = embed_run(run_id, client)
    finally:  # 호출 기록(상태·오류 종류·입력 해시. 입력 글자는 없다)을 남겨 실패 원인을 볼 수 있게 한다.
        (Path(out) / "embedding-audit.json").write_text(json.dumps(client.audit, ensure_ascii=False), encoding="utf8")
    return dict(run_id=run_id, profile_id=done["profile_id"], provider=config.provider,
                input_tokens=sum(c.get("input_tokens") or 0 for c in done["calls"]),
                estimated_cost_usd=round(sum(c.get("estimated_cost_usd") or 0 for c in done["calls"]), 6),
                search_entries=done["total"], reused=done["reused"],
                copied_from_other_runs=done["copied_from_other_runs"], written=done["written"],
                skipped=len(done["skipped"]), embedding_calls=len(done["calls"]),
                coverage_complete=done["coverage_complete"])


def run_and_measure(out, action):
    """작업 프로세스 안에서 실행된다: 단계 함수를 부르고, 돌려준 요약과 자기 최대 메모리를 적는다."""
    try:
        result = action()
        if result is not None:
            (Path(out) / "stage-result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf8")
    finally:
        (Path(out) / "worker-usage.json").write_text(json.dumps(dict(peak_rss_bytes=own_peak_rss_bytes())))


def tally(values):
    counted = {}
    for value in values:
        counted[str(value)] = counted.get(str(value), 0) + 1
    return counted


def summarize(out):
    """분석 결과에서 규모와 단계별 시간을 읽는다. 값을 고르거나 고치지 않는다."""
    result = json.loads((out / "result.json").read_text(encoding="utf8"))
    summary = dict(
        tables=len(result["tables"]), cells=sum(len(t["cells"]) for t in result["tables"]),
        table_states=tally(t.get("state") for t in result["tables"]),
        page_text_status=tally(p.get("text_status") for p in result["pages"]),
        processing_status=result.get("processing_status"))
    progress = out / "run-progress.json"
    if progress.is_file():
        stages = json.loads(progress.read_text(encoding="utf8")).get("stages", [])
        summary["stage_seconds"] = {s["id"]: s["seconds"] for s in stages if "seconds" in s}
    return summary


def stage_summary(out):
    return json.loads((out / "stage-result.json").read_text(encoding="utf8"))


def run_job(job, queue, *, output, base_seconds, seconds_per_page, command=None, stop=None,
            heartbeat_seconds=10, poll_seconds=0.5, embedding=None, seconds_per_1000_cells=60, seconds_per_entry=2):
    """작업 하나를 실행하고 결과 요약을 돌려준다. 실패는 JobError, 소유권을 잃었거나 멈춤 요청이면 None."""
    if job["stage"] == "analyze":
        pages, size = inspect(job["source_path"], job["document_sha256"])
    else:  # 쪽 수와 크기는 분석 단계가 잰 값을 이어 쓴다.
        pages, size = job["payload"]["input"]["pages"], job["payload"]["input"]["bytes"]
    limit = stage_time_limit(job, pages, base_seconds, seconds_per_page, seconds_per_1000_cells, seconds_per_entry)
    # 폴더 이름은 짧게 둔다. 분석 결과가 아래에 긴 경로를 만들고 Windows는 경로 길이 제한이 있다.
    # runs는 줄지 않는다. 멈춘 뒤 재개하거나 격리 후 다시 넣어도 이전 실행의 로그·산출물을 덮어쓰지 않는다.
    out = Path(output) / job["queue"] / str(job["job_id"]) / f"r{job['runs']}"
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    with open(out / "worker-stderr.txt", "wb") as errors:
        process = subprocess.Popen((command or stage_command)(job, out, embedding or LOCAL_EMBEDDING), cwd=ROOT,
                                   stdout=subprocess.DEVNULL, stderr=errors)
        beat = started
        try:
            while process.poll() is None:
                time.sleep(poll_seconds)
                now = time.monotonic()
                if stop is not None and stop.is_set():
                    return None
                if now - started > limit:
                    raise JobError("TIMEOUT", f"{limit}s for {pages} pages")
                if now - beat >= heartbeat_seconds:
                    beat = now
                    if not queue.heartbeat(job):
                        return None
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    seconds = round(time.monotonic() - started, 3)
    if process.returncode:
        lines = (out / "worker-stderr.txt").read_text(encoding="utf8", errors="replace").splitlines()
        # 예외 종류만 남긴다(들여쓰지 않은 마지막 줄의 ':' 앞). 메시지에는 문서 글자가 섞일 수 있다.
        last = next((line for line in reversed(lines) if line and not line[0].isspace()), "")
        raise JobError(FAILED[job["stage"]], last.split(":")[0].strip() or f"exit {process.returncode}")
    try:
        summary = (summarize if job["stage"] == "analyze" else stage_summary)(out)
    except Exception:
        raise JobError("RESULT_UNREADABLE") from None
    usage = out / "worker-usage.json"
    peak = json.loads(usage.read_text(encoding="utf8")).get("peak_rss_bytes") if usage.is_file() else None
    return dict(summary, pages=pages, bytes=size, seconds=seconds, time_limit_seconds=limit,
                peak_rss_mb=round(peak / 1048576, 1) if peak else None, output=str(out))


def following(job, result):
    """등록할 때 정한 단계(payload["through"])까지 남았으면 다음 단계 이름과 그 입력. 아니면 (None, None)."""
    through = job["payload"].get("through", job["stage"])
    position = STAGES.index(job["stage"])
    if position >= STAGES.index(through):
        return None, None
    return STAGES[position + 1], dict(through=through, input=result)


def worker_loop(name, queue_name, queue, stop, log, *, stage=STAGE, upstream=(), **options):
    """대기 작업이 없어질 때까지 하나씩 집어 실행한다. 다른 작업자가 실행 중이면 재시도분이 생길 수 있어 기다린다.

    upstream: 같은 명령이 함께 돌리는 앞 단계. 앞 단계가 끝나지 않았으면 일이 더 올 수 있으므로 기다린다.
    """
    stale = options.pop("stale_seconds")
    retry_delay = options.pop("retry_delay_seconds")
    idle = options.pop("idle_seconds", 2)
    while not stop.is_set():
        queue.reclaim(queue_name, stale)
        job = queue.claim(queue_name, stage, name)
        if job is None:
            if not queue.pending(queue_name, [stage, *upstream]):
                return
            stop.wait(idle)
            continue
        try:
            result = run_job(job, queue, stop=stop, **options)
        except JobError as error:
            state = queue.fail(job, error.kind, error.detail, retry=error.kind not in PERMANENT,
                               delay_seconds=retry_delay)
            log(f"{name} job {job['job_id']} {error.kind} -> {state}")
            continue
        except Exception as error:  # 작업자 자신의 오류로 줄이 실행 중에 남지 않게 한다.
            state = queue.fail(job, "WORKER_ERROR", type(error).__name__, delay_seconds=retry_delay)
            log(f"{name} job {job['job_id']} WORKER_ERROR -> {state}")
            continue
        if result is None:
            queue.release(job)
            continue
        next_stage, next_payload = following(job, result)
        if queue.complete(job, result, next_stage=next_stage, next_payload=next_payload):
            log(f"{name} job {job['job_id']} done {result['pages']}p {result['seconds']}s")


def prepare_schemas(stages):
    """색인·임베딩 표를 작업자를 띄우기 전에 한 번만 준비한다(작업 프로세스마다 스키마 문장을 돌리지 않는다)."""
    if "index" in stages or "embed" in stages:
        from doc_understanding.rag import catalog, repository

        repository.migrate()
        catalog.ensure()
    if "embed" in stages:
        from doc_understanding.rag.embedding import repository as vectors

        vectors.migrate()


class WorkFailed(RuntimeError):
    """작업자가 오류로 끝났거나 끝나지 않은 작업이 남았다."""


def guarded(failures, name, log, *args, **options):
    try:
        worker_loop(name, *args, log, **options)
    except Exception as error:  # 큐 조회 오류 등. 종류만 남기고 work()가 실패로 알린다.
        failures.append(type(error).__name__)
        log(f"{name} stopped: {type(error).__name__}")


def work(queue_name, workers, *, queue=None, log=print, **options):
    """작업자들이 모두 끝날 때까지 기다린다. 오류로 끝난 작업자나 남은 작업이 있으면 WorkFailed.

    workers: 분석 작업자 수, 또는 단계별 작업자 수(dict). 0인 단계는 돌리지 않는다.
    """
    workers = {k: v for k, v in (workers if isinstance(workers, dict) else {STAGE: workers}).items() if v}
    stages = [s for s in STAGES if s in workers]
    if queue is None:
        from doc_understanding.runtime import job_queue as queue

        queue.migrate()
        prepare_schemas(stages)
    stop = threading.Event()
    host = f"{socket.gethostname()}:{os.getpid()}"
    failures = []
    threads = [threading.Thread(target=guarded, args=(failures, f"{host}:{stage}:{n}", log, queue_name, queue, stop),
                                kwargs=dict(options, stage=stage, upstream=stages[:stages.index(stage)]), daemon=True)
               for stage in stages for n in range(workers[stage])]
    started = time.monotonic()
    for thread in threads:
        thread.start()
    try:
        while any(thread.is_alive() for thread in threads):
            time.sleep(0.2)
    except KeyboardInterrupt:
        stop.set()  # 실행 중인 분석을 끝내고 작업을 대기로 돌려놓는다.
        for thread in threads:
            thread.join()
        raise
    if failures:
        raise WorkFailed(f"{len(failures)} of {len(threads)} workers stopped: {', '.join(sorted(set(failures)))}")
    left = queue.pending(queue_name, stages)
    if left:
        raise WorkFailed(f"{left} jobs not finished")
    return round(time.monotonic() - started, 3)


def percentile(values, share):
    values = sorted(values)
    return values[min(len(values) - 1, int(share * len(values)))] if values else None


def spread(values, digits=2):
    values = [v for v in values if v is not None]
    if not values:
        return None
    return dict(n=len(values), median=round(statistics.median(values), digits), p90=round(percentile(values, 0.9), digits),
                max=round(max(values), digits))


def merge(tallies):
    total = {}
    for counted in tallies:
        for key, value in counted.items():
            total[key] = total.get(key, 0) + value
    return total


def report(job_rows):
    """큐의 줄로 처리량·시간·실패를 요약한다. 분모(전체 줄 수)에서 실패를 빼지 않는다."""
    done = [r for r in job_rows if r["status"] == "done"]
    results = [r["result"] for r in done]
    statuses, kinds = {}, {}
    for row in job_rows:
        statuses[row["status"]] = statuses.get(row["status"], 0) + 1
        for error in row["errors"]:
            kinds[error["kind"]] = kinds.get(error["kind"], 0) + 1
    pages = sum(r["pages"] for r in results)
    busy = sum(r["seconds"] for r in results)
    starts = [r["started_at"] for r in done if r["started_at"]]
    ends = [r["finished_at"] for r in done if r["finished_at"]]
    wall = None
    if starts and ends:
        from datetime import datetime

        wall = round((datetime.fromisoformat(max(ends)) - datetime.fromisoformat(min(starts))).total_seconds(), 1)
    return dict(
        documents=len(job_rows), statuses=statuses, error_attempts_by_kind=kinds,
        retried_then_done=sum(r["attempts"] > 1 for r in done), workers_seen=len({r["worker"] for r in done}),
        pages=pages, tables=sum(r["tables"] for r in results), cells=sum(r["cells"] for r in results),
        table_states=merge(r["table_states"] for r in results),
        page_text_status=merge(r["page_text_status"] for r in results),
        processing_status=tally(r["processing_status"] for r in results),
        analysis_seconds_total=round(busy, 1), wall_seconds_first_start_to_last_finish=wall,
        pages_per_minute_wall=round(pages / wall * 60, 1) if wall else None,
        seconds_per_document=spread([r["seconds"] for r in results]),
        seconds_per_page=spread([r["seconds"] / r["pages"] for r in results], 3),
        seconds_per_1000_cells=spread([r["seconds"] / r["cells"] * 1000 for r in results if r["cells"]], 2),
        pages_per_document=spread([r["pages"] for r in results], 0),
        peak_rss_mb=spread([r["peak_rss_mb"] for r in results], 1),
        slowest=[dict(name=Path(r["source_path"]).name, pages=r["result"]["pages"], seconds=r["result"]["seconds"],
                      tables=r["result"]["tables"], cells=r["result"]["cells"])
                 for r in sorted(done, key=lambda r: -r["result"]["seconds"])[:10]],
        not_done=[dict(name=Path(r["source_path"]).name, status=r["status"], error_kind=r["error_kind"],
                       errors=r["errors"]) for r in job_rows if r["status"] != "done"])


def stage_report(job_rows):
    """색인·임베딩 단계 요약. 결과의 숫자 항목은 합계를 낸다."""
    done = [r for r in job_rows if r["status"] == "done"]
    results = [r["result"] for r in done]
    statuses, kinds, totals = {}, {}, {}
    for row in job_rows:
        statuses[row["status"]] = statuses.get(row["status"], 0) + 1
        for error in row["errors"]:
            kinds[error["kind"]] = kinds.get(error["kind"], 0) + 1
    for result in results:
        for key, value in result.items():
            if isinstance(value, int) and not isinstance(value, bool) and key not in ("bytes", "time_limit_seconds"):
                totals[key] = totals.get(key, 0) + value
    wall = None
    if done:
        from datetime import datetime

        wall = round((datetime.fromisoformat(max(r["finished_at"] for r in done))
                      - datetime.fromisoformat(min(r["started_at"] for r in done))).total_seconds(), 1)
    entries = totals.get("search_entries")
    return dict(
        documents=len(job_rows), statuses=statuses, error_attempts_by_kind=kinds, totals=totals,
        workers_seen=len({r["worker"] for r in done}), stage_seconds_total=round(sum(r["seconds"] for r in results), 1),
        wall_seconds_first_start_to_last_finish=wall,
        documents_per_minute_wall=round(len(done) / wall * 60, 1) if wall else None,
        search_entries_per_minute_wall=round(entries / wall * 60, 1) if wall and entries else None,
        seconds_per_document=spread([r["seconds"] for r in results]),
        seconds_per_1000_entries=spread([r["seconds"] / r["search_entries"] * 1000 for r in results
                                         if r.get("search_entries")], 2),
        peak_rss_mb=spread([r["peak_rss_mb"] for r in results], 1),
        incomplete_embedding=sum(r.get("coverage_complete") is False for r in results),
        structure_review=tally(r["structure_review"]["status"] for r in results if "structure_review" in r),
        slowest=[dict(name=Path(r["source_path"]).name, seconds=r["result"]["seconds"],
                      search_entries=r["result"].get("search_entries"))
                 for r in sorted(done, key=lambda r: -r["result"]["seconds"])[:10]],
        not_done=[dict(name=Path(r["source_path"]).name, status=r["status"], error_kind=r["error_kind"],
                       errors=r["errors"]) for r in job_rows if r["status"] != "done"])


# 격리 사유별로 사람이 할 일. 보고서에만 쓴다.
ADVICE = dict(
    NOT_PDF="PDF가 아니다(확장자만 PDF). 원본 형식을 확인한다",
    ENCRYPTED="암호가 걸려 있다. 암호를 푼 파일로 다시 넣는다",
    UNREADABLE="파일을 열 수 없다(손상). 원본을 다시 받는다",
    NO_PAGES="쪽이 없다",
    SOURCE_MISSING="등록할 때의 경로에 파일이 없다. 파일을 되돌리거나 다시 등록한다",
    SOURCE_CHANGED="등록한 뒤 파일 내용이 바뀌었다. 다시 등록한다",
    TIMEOUT="제한 시간을 넘겼다. 시간 인자를 늘려 retry --kind TIMEOUT",
    ANALYSIS_FAILED="분석이 오류로 끝났다. 산출물 폴더의 worker-stderr.txt를 본다",
    INDEX_FAILED="색인이 오류로 끝났다. 산출물 폴더의 worker-stderr.txt를 본다",
    EMBED_FAILED="임베딩이 오류로 끝났다. 산출물 폴더의 embedding-audit.json을 본다(서버·모델·입력 길이)",
    RESULT_UNREADABLE="작업은 끝났지만 결과 파일을 읽지 못했다. retry로 다시 실행한다",
    WORKER_ERROR="작업자 쪽 오류. retry로 다시 실행한다",
    WORKER_LOST="작업자가 응답 없이 사라졌다(메모리 부족·강제 종료). 작업자 수를 줄여 retry",
)
NOT_DONE_COLUMNS = ("stage", "status", "error_kind", "file", "advice", "attempts", "last_detail", "folder", "source_path")


def not_done_table(rows_by_stage):
    """끝나지 않은 작업을 단계 순서대로 한 줄씩. 사람이 엑셀로 열어 볼 표다."""
    table = []
    for stage in STAGES:
        for row in rows_by_stage.get(stage, []):
            if row["status"] == "done":
                continue
            errors = row["errors"] or []
            table.append(dict(
                stage=stage, status=row["status"], error_kind=row["error_kind"] or "", file=Path(row["source_path"]).name,
                advice=ADVICE.get(row["error_kind"], "") if row["status"] == "quarantined" else "",
                attempts=row["attempts"], last_detail=errors[-1].get("detail", "") if errors else "",
                folder=f"{row['job_id']}/r{row['runs']}" if row.get("runs") else "", source_path=row["source_path"]))
    return table


def write_not_done(path, table):
    import csv

    with open(path, "w", encoding="utf-8-sig", newline="") as stream:  # BOM: 엑셀이 한글을 그대로 읽는다
        writer = csv.DictWriter(stream, fieldnames=NOT_DONE_COLUMNS)
        writer.writeheader()
        writer.writerows(table)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["_analyze"]:  # 아래 셋은 작업자가 띄운 작업 프로세스
        return run_and_measure(argv[2], lambda: analyze_document(argv[1], argv[2]))
    if argv[:1] == ["_index"]:
        return run_and_measure(argv[2], lambda: index_document(argv[1], argv[2], argv[3]))
    if argv[:1] == ["_store"]:
        return run_and_measure(argv[3], lambda: store_document(argv[1], argv[2], argv[4], argv[5]))
    if argv[:1] == ["_embed"]:
        return run_and_measure(argv[2], lambda: embed_document(argv[1], argv[2], argv[3]))
    from doc_understanding.runtime import job_queue

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("enqueue", "enqueue-reviewed", "work", "status", "report", "retry"):
        commands.add_parser(name).add_argument("--queue", required=True)
    reviewed = commands.choices["enqueue-reviewed"]
    reviewed.add_argument("--manifest", type=Path, required=True,
                          help="JSON 목록: source_path, source_sha256, manifest_path, document_path, (선택) name, "
                               "previous_run_id, pages, cells")
    reviewed.add_argument("--through", choices=STAGES[1:], default="index")
    reviewed.add_argument("--max-attempts", type=int, default=2)
    enqueue, worker, reporter, retry = (commands.choices[n] for n in ("enqueue", "work", "report", "retry"))
    enqueue.add_argument("paths", nargs="+", type=Path)
    enqueue.add_argument("--max-attempts", type=int, default=2)
    enqueue.add_argument("--through", choices=STAGES, default=STAGE, help="이 단계까지 자동으로 이어서 처리한다")
    for command in (reporter, retry):
        command.add_argument("--stage", choices=STAGES, default=STAGE)
    worker.add_argument("--workers", type=int, default=2, help="분석 작업자 수")
    worker.add_argument("--index-workers", type=int, default=0)
    worker.add_argument("--embed-workers", type=int, default=0)
    worker.add_argument("--embedding-provider", choices=("local", "openai", "compatible", "none"),
                        help="주지 않으면 설치 설정 파일, 없으면 local. compatible은 OpenAI 형식 서버(<base-url>/embeddings)")
    worker.add_argument("--embedding-base-url", help="compatible 서버 주소(예: http://10.0.0.5:8000/v1) 또는 local Ollama 주소")
    worker.add_argument("--embedding-api-key-env", help="compatible 서버의 키가 든 환경변수 이름")
    worker.add_argument("--embedding-model", help="기본: local bge-m3, openai text-embedding-3-small. compatible은 필수")
    worker.add_argument("--embedding-dimensions", type=int, help="기본: local 1024, openai 1536. compatible은 필수")
    worker.add_argument("--embedding-max-input-bytes", type=int, help="이보다 긴 검색 항목은 임베딩하지 않는다(기본: local 4500, 그 외 7500)")
    worker.add_argument("--allow-external", action="store_true", help="검색 항목 글자를 이 컴퓨터 밖의 서버로 보내는 데 동의한다")
    worker.add_argument("--embedding-usd-per-million", type=float, help="임베딩 단가(100만 토큰당 달러). openai는 필수")
    worker.add_argument("--embedding-max-cost-usd", type=float, help="문서 하나당 임베딩 비용 한도(기본 1.0)")
    worker.add_argument("--output", type=Path, default=ROOT / "work" / "batch-ingest")
    worker.add_argument("--base-seconds", type=float, default=120)
    worker.add_argument("--seconds-per-page", type=float, default=20)
    worker.add_argument("--seconds-per-1000-cells", type=float, default=60, help="색인 제한 시간에 칸 1,000개마다 더하는 초")
    worker.add_argument("--seconds-per-entry", type=float, default=2, help="임베딩 제한 시간에 검색 항목마다 더하는 초")
    worker.add_argument("--stale-seconds", type=float, default=120)
    worker.add_argument("--retry-delay-seconds", type=float, default=30)
    reporter.add_argument("--output", type=Path, required=True)
    retry.add_argument("--kind", action="append")
    args = parser.parse_args(argv)
    if args.command == "work":
        try:
            embedding = embedding_settings(
                args.embedding_provider, model=args.embedding_model, dimensions=args.embedding_dimensions,
                max_input_bytes=args.embedding_max_input_bytes, base_url=args.embedding_base_url,
                api_key_env=args.embedding_api_key_env, allow_external=args.allow_external,
                usd_per_million=args.embedding_usd_per_million, max_cost_usd=args.embedding_max_cost_usd)
        except ValueError as error:  # 설정 오류는 종류만 보인다(pydantic 오류는 첫 줄).
            parser.error(str(error).splitlines()[0])
        if embedding is None and args.embed_workers:
            parser.error("EMBEDDING_NOT_CONFIGURED: 임베딩 설정이 none이면 --embed-workers를 쓸 수 없다")
    job_queue.migrate()
    if args.command == "enqueue":
        items, duplicates = scan(args.paths)
        items = [dict(item, payload=dict(through=args.through)) for item in items]
        added = job_queue.enqueue(args.queue, STAGE, items, max_attempts=args.max_attempts)
        print(json.dumps(dict(found=len(items) + duplicates, same_content_skipped=duplicates,
                              added=len(added), already_queued=len(items) - len(added))))
    elif args.command == "enqueue-reviewed":
        try:
            items = reviewed_items(json.loads(args.manifest.read_text(encoding="utf-8-sig")), args.through)
        except ValueError as error:
            parser.error(str(error))
        added = job_queue.enqueue(args.queue, "index", items, max_attempts=args.max_attempts)
        print(json.dumps(dict(found=len(items), added=len(added), already_queued=len(items) - len(added))))
    elif args.command == "work":
        try:
            seconds = work(args.queue, dict(analyze=args.workers, index=args.index_workers, embed=args.embed_workers),
                           output=args.output, base_seconds=args.base_seconds,
                           seconds_per_page=args.seconds_per_page, stale_seconds=args.stale_seconds,
                           retry_delay_seconds=args.retry_delay_seconds,
                           seconds_per_1000_cells=args.seconds_per_1000_cells, seconds_per_entry=args.seconds_per_entry,
                           embedding=embedding)
        except WorkFailed as error:
            print(f"work failed: {error}", file=sys.stderr)
            return 1
        print(json.dumps(dict(wall_seconds=seconds, counts=job_queue.counts(args.queue))))
    elif args.command == "status":
        print(json.dumps(job_queue.counts(args.queue), ensure_ascii=False, indent=1))
    elif args.command == "retry":
        print(json.dumps(dict(requeued=job_queue.requeue(args.queue, args.stage, kinds=args.kind))))
    else:
        args.output.mkdir(parents=True, exist_ok=False)
        job_rows = job_queue.rows(args.queue, args.stage)
        summary = (report if args.stage == STAGE else stage_report)(job_rows)
        (args.output / "jobs.json").write_text(json.dumps(job_rows, ensure_ascii=False, indent=1), encoding="utf8")
        (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf8")
        print(json.dumps({k: v for k, v in summary.items() if k not in ("slowest", "not_done")}, ensure_ascii=False, indent=1))
        # 끝나지 않은 문서는 --stage와 상관없이 모든 단계를 한 표로 낸다.
        table = not_done_table({stage: job_queue.rows(args.queue, stage) for stage in STAGES})
        write_not_done(args.output / "not-done.csv", table)
        counted = tally(f"{row['stage']} {row['status']} {row['error_kind']}".strip() for row in table)
        print(f"not done: {len(table)} -> {args.output / 'not-done.csv'}")
        for key, count in sorted(counted.items()):
            print(f"  {count:5d}  {key}  {ADVICE.get(key.split()[-1], '')}")


if __name__ == "__main__":
    sys.exit(main())
