"""Local-only document explorer. Original pipeline is imported, never modified."""

import hashlib
import json
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import unquote

import fitz
from PIL import Image
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, ConfigDict
from starlette.middleware.trustedhost import TrustedHostMiddleware

from doc_understanding.experiments.parser_repair import engine
from doc_understanding.experiments.parser_repair import queue as parser_experiments_queue
from doc_understanding.review import error_basket
from doc_understanding.runtime import review_queue
from doc_understanding.runtime.paths import ROOT
from doc_understanding.runtime.catalogs import sample_catalog, campaign_manifest
from doc_understanding.services.documents import worker as worker
from doc_understanding.web.rag_routes import router as rag_router
from doc_understanding.web.mapping_routes import router as mapping_router
from doc_understanding.web.search_routes import router as search_router

HERE = Path(__file__).resolve().parent / "static"
DATA = ROOT / "work/document-explorer"
DATA.mkdir(parents=True, exist_ok=True)
TOKEN = secrets.token_urlsafe(32)
JOBS = {}
LOCK = threading.Lock()
POOL = ThreadPoolExecutor(max_workers=1)
REVIEW_QUEUE = None
REPAIR_QUEUE = None
CAMPAIGN_LOCK = threading.Lock()
CAMPAIGN_RUNNING = False
RAG_SERVICE = None


def get_rag_service():
    global RAG_SERVICE
    # API 초기화가 겹쳐 작업자가 둘 생기지 않도록 기존 서버 잠금을 사용한다.
    with LOCK:
        if RAG_SERVICE is None:
            from doc_understanding.services.web_rag import WebRagService
            RAG_SERVICE = WebRagService(DATA, ROOT / "work/web-rag")
        return RAG_SERVICE


def get_review_queue():
    global REVIEW_QUEUE
    if REVIEW_QUEUE is None:
        REVIEW_QUEUE = review_queue.ReviewQueue(DATA)
    return REVIEW_QUEUE


def get_repair_queue():
    global REPAIR_QUEUE
    reviews = get_review_queue()
    if REPAIR_QUEUE is None or REPAIR_QUEUE.reviews is not reviews:
        if REPAIR_QUEUE is not None:
            REPAIR_QUEUE.close()
        pass
        REPAIR_QUEUE = parser_experiments_queue.RepairQueue(reviews.data, reviews)
    return REPAIR_QUEUE


@asynccontextmanager
async def lifespan(app):
    recover_jobs()
    queue = get_review_queue()
    queue.start()
    repair = get_repair_queue()
    repair.start()
    try:
        yield
    finally:
        if RAG_SERVICE is not None:
            RAG_SERVICE.close()
        repair.close()
        queue.close()
        from doc_understanding.storage.staging.local import close_pool
        close_pool()  # DB 커넥션 풀을 닫는다(풀을 쓰지 않았으면 아무 일도 하지 않음).


app = FastAPI(docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])


def process(jid):
    job = JOBS[jid]
    out = DATA / jid
    start = time.perf_counter()
    try:
        job["status"] = "running"
        r = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "doc_understanding.services.documents",
                str(out / "input.pdf"),
                str(out),
            ],
            cwd=ROOT,
            capture_output=True,
            timeout=180,
        )
        if r.returncode:
            raise RuntimeError("분석을 완료하지 못했습니다. 다른 PDF로 다시 시도해 주세요.")
        result = json.loads((out / "result.json").read_text(encoding="utf8"))
        job.update(status="done", result=result, seconds=round(time.perf_counter() - start, 2))
    except subprocess.TimeoutExpired:
        job.update(
            status="error",
            error="분석 시간이 3분을 초과했습니다. 페이지 수가 적은 PDF로 시도해 주세요.",
        )
    except Exception:
        job.update(
            status="error", error="분석을 완료하지 못했습니다. 다른 PDF로 다시 시도해 주세요."
        )


def validate_pdf(data):
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "20MB 이하 PDF를 선택해 주세요.")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(415, "PDF 파일만 지원합니다.")
    try:
        with fitz.open(stream=data, filetype="pdf") as d:
            if d.needs_pass:
                raise HTTPException(422, "암호가 없는 PDF를 선택해 주세요.")
            if not 1 <= len(d) <= 20:
                raise HTTPException(422, "1~20쪽 PDF를 선택해 주세요.")
            return len(d)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, "읽을 수 없는 PDF입니다.") from None


def start(data, name, *, parent_run_id=None):
    source = data
    data, extension = normalize_upload(data)
    pages = validate_pdf(data)
    with LOCK:
        if any(j["status"] in ("queued", "running") for j in JOBS.values()):
            raise HTTPException(429, "현재 문서를 분석 중입니다. 완료 후 다시 시도해 주세요.")
        jid = uuid.uuid4().hex
        folder = DATA / jid
        folder.mkdir()
        (folder / "input.pdf").write_bytes(data)
        if extension != "pdf":
            (folder / ("original." + extension)).write_bytes(source)
        provenance = dict(
            source_type=extension,
            source_sha256=hashlib.sha256(source).hexdigest(),
            analysis_sha256=hashlib.sha256(data).hexdigest(),
            conversion="image-only PDF; no OCR" if extension != "pdf" else None,
        )
        (folder / "source.json").write_text(json.dumps(provenance), encoding="utf8")
        info = {"name": name[:150], "pages": pages, "parent_run_id": parent_run_id}
        (folder / "job-info.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf8")
        JOBS[jid] = dict(id=jid, status="queued", **info, **provenance)
    POOL.submit(process, jid)
    return dict(id=jid, name=name, pages=pages, status="queued")


def normalize_upload(data):
    """Preserve uploaded pixels; conversion does not imply text recognition."""
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "20MB 이하 파일을 선택해 주세요.")
    if data.startswith(b"%PDF-"):
        validate_pdf(data)
        return data, "pdf"
    kind = (
        "png"
        if data.startswith(b"\x89PNG\r\n\x1a\n")
        else "jpeg"
        if data.startswith(b"\xff\xd8\xff")
        else None
    )
    if not kind:
        raise HTTPException(415, "PDF, PNG 또는 JPG 이미지를 선택해 주세요.")
    try:
        with fitz.open(stream=data, filetype=kind) as image:
            # 픽셀 전체를 해제하기 전에 이미지 헤더의 실제 크기를 확인한다.
            # PyMuPDF의 Windows image_profile 바인딩 오류와 분리해 검사한다.
            with Image.open(BytesIO(data)) as header:
                width, height = header.size
            if (
                width * height > 12_000_000
                or max(width, height) > 8000
            ):
                raise HTTPException(
                    413, "이미지는 1,200만 화소, 한 변 8,000픽셀 이하로 선택해 주세요."
                )
            return image.convert_to_pdf(), kind
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            422, "읽을 수 없는 이미지입니다. PNG 또는 JPG로 다시 저장해 주세요."
        ) from None


def authorize(request):
    if request.headers.get("x-demo-token") != TOKEN:
        raise HTTPException(403, "페이지를 새로고침해 주세요.")
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "허용되지 않은 요청입니다.")


@app.get("/")
def home():
    return HTMLResponse(
        (HERE / "index.html").read_text(encoding="utf8").replace("__TOKEN__", TOKEN).replace(
            "</body>", '<script src="/campaign.js"></script></body>'
        )
    )


@app.get("/campaign.js")
def campaign_script():
    return FileResponse(HERE / "campaign.js", media_type="application/javascript")


@app.get("/api/campaigns")
def campaigns():
    from doc_understanding.agents.workflows.campaign.store import CampaignStore
    root = ROOT / "work/campaigns"
    records = CampaignStore(root).list_campaigns()
    for record in records:
        folder = root / "pilots" / record["id"]
        if (folder / "spec.json").exists():
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf8"))
            record["pilot"] = {"selected": True, "human_approved": False, "parser_modified": False,
                               "planned_items": len(manifest['items'])}
            if (folder / "specialist.json").exists():
                record["pilot"].update(json.loads((folder / "specialist.json").read_text(encoding="utf8")))
    queue_path = root / "review-queue/queue.sqlite3"
    if queue_path.exists():
        with sqlite3.connect(queue_path) as db:
            rows = {r[0]: {"status": r[1], "mode": r[2]} for r in db.execute(
                "SELECT id,status,mode FROM requests")}
        for record in records:
            for request in record["review_requests"]:
                request.update(rows.get(request["request_id"], {"status": "unknown"}))
    return {"running": CAMPAIGN_RUNNING, "campaigns": records}


@app.get("/api/campaigns/{campaign_id}/local-reviews")
def campaign_local_reviews(campaign_id: str):
    from doc_understanding.agents.workflows.campaign.store import CampaignStore
    try:
        campaign = CampaignStore(ROOT / "work/campaigns").get(campaign_id)
    except ValueError:
        raise HTTPException(404, "작업을 찾을 수 없습니다.") from None
    reports = []
    for sample in campaign["samples"]:
        if sample["review_status"] != "completed":
            continue
        try:
            report = json.loads((Path(sample["output"]) / "campaign-review.json").read_text(encoding="utf8"))
            reports.append({"name": Path(sample["path"]).name,
                            "count": len(report["findings"]), "findings": report["findings"][:10]})
        except (OSError, ValueError, KeyError, TypeError):
            reports.append({"name": Path(sample["path"]).name, "unavailable": True})
    return reports


@app.post("/api/campaigns/automatic")
def start_automatic_campaign(request: Request):
    authorize(request)
    try:
        manifest = campaign_manifest(ROOT)
    except (OSError, ValueError):
        raise HTTPException(422, "설정한 캠페인 표본 파일을 확인해 주세요.") from None
    global CAMPAIGN_RUNNING
    with CAMPAIGN_LOCK:
        if CAMPAIGN_RUNNING:
            raise HTTPException(409, "이미 자동 평가가 진행 중입니다.")
        CAMPAIGN_RUNNING = True

    def run():
        global CAMPAIGN_RUNNING
        try:
            from doc_understanding.agents.workflows.campaign.service import execute_local_cycle
            from doc_understanding.agents.workflows.campaign.store import CampaignStore
            store = CampaignStore(ROOT / "work/campaigns")
            if manifest is not None:
                from doc_understanding.agents.workflows.campaign import pilot
                cid = pilot.prepare(store, manifest, ROOT)
                if store.get(cid)["status"] == "ready":
                    pilot.run(store, cid, ROOT)
            else:
                execute_local_cycle(store, ROOT / "data/campaign-inbox")
        finally:
            with CAMPAIGN_LOCK:
                CAMPAIGN_RUNNING = False

    try:
        threading.Thread(target=run, daemon=True).start()
    except Exception:
        with CAMPAIGN_LOCK:
            CAMPAIGN_RUNNING = False
        raise
    return {"started": True}


@app.post("/api/campaigns/{campaign_id}/resume")
def resume_campaign(campaign_id: str, request: Request, retry_failed: bool = False):
    authorize(request)
    from doc_understanding.agents.workflows.campaign import pilot
    from doc_understanding.agents.workflows.campaign.store import CampaignStore
    store = CampaignStore(ROOT / "work/campaigns")
    try:
        record = store.get(campaign_id)
    except ValueError:
        raise HTTPException(404, "캠페인을 찾을 수 없습니다.") from None
    if not (store.root / "pilots" / campaign_id / "spec.json").is_file():
        raise HTTPException(409, "선택 표본 캠페인만 재개할 수 있습니다.")
    if record["status"] not in ("paused", "blocked", "running"):
        raise HTTPException(409, "재개할 수 있는 상태가 아닙니다.")
    global CAMPAIGN_RUNNING
    with CAMPAIGN_LOCK:
        if CAMPAIGN_RUNNING:
            raise HTTPException(409, "이미 캠페인을 실행 중입니다.")
        CAMPAIGN_RUNNING = True

    def run():
        global CAMPAIGN_RUNNING
        try:
            pilot.run(store, campaign_id, ROOT, resume=True, retry_failed=retry_failed)
        except Exception as exc:
            store.event(campaign_id, "supervisor", "resume_failed:" + type(exc).__name__)
        finally:
            with CAMPAIGN_LOCK:
                CAMPAIGN_RUNNING = False
    try:
        threading.Thread(target=run, daemon=True).start()
    except Exception:
        with CAMPAIGN_LOCK:
            CAMPAIGN_RUNNING = False
        raise
    return {"started": True, "campaign_id": campaign_id}


@app.get("/monitor")
def monitor():
    return HTMLResponse(
        (HERE / "monitor.html").read_text(encoding="utf8").replace("__TOKEN__", TOKEN)
    )


@app.get("/monitor.css")
def monitor_style():
    return FileResponse(HERE / "monitor.css", media_type="text/css")


@app.get("/explorer_embed.css")
def explorer_embed_style():
    return FileResponse(HERE / "explorer_embed.css", media_type="text/css")


@app.get("/monitor.js")
def monitor_script():
    return FileResponse(HERE / "monitor.js", media_type="application/javascript")


@app.get("/monitor_model.js")
def monitor_model_script():
    return FileResponse(HERE / "monitor_model.js", media_type="application/javascript")


@app.get("/repair_monitor.js")
def repair_monitor_script():
    return FileResponse(HERE / "repair_monitor.js", media_type="application/javascript")


@app.get("/repair_metrics.js")
def repair_metrics_script():
    return FileResponse(HERE / "repair_metrics.js", media_type="application/javascript")


@app.get("/review.js")
def review_script():
    return FileResponse(HERE / "review.js", media_type="application/javascript")


@app.get("/basket.js")
def basket_script():
    return FileResponse(HERE / "basket.js", media_type="application/javascript")


@app.get("/reference.js")
def reference_script():
    return FileResponse(HERE / "reference.js", media_type="application/javascript")


@app.get("/reference.css")
def reference_style():
    return FileResponse(HERE / "reference.css", media_type="text/css")


@app.get("/queue.js")
def queue_script():
    return FileResponse(HERE / "queue.js", media_type="application/javascript")


@app.get("/queue.css")
def queue_style():
    return FileResponse(HERE / "queue.css", media_type="text/css")


@app.get("/decision.js")
def decision_script():
    return FileResponse(HERE / "decision.js", media_type="application/javascript")


class QueueControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paused: bool


class QueueAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["cancel", "retry"]


class AgentControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    choice: Literal["agree", "disagree", "uncertain"]
    note: str
    source_ids: list[str] = []


@app.post("/api/review-queue/{rid}/decision")
def queue_decision(rid: str, payload: ReviewDecision, request: Request):
    authorize(request)
    if not 1 <= len(payload.note.strip()) <= 4000 or len(payload.source_ids) > 24:
        raise HTTPException(422, "근거 메모는 1~4,000자, 원본 ID는 24개 이하로 입력해주세요.")
    child = get_review_queue().decide(rid, payload.model_dump())
    return get_review_queue().detail(child)


class ReviewConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: bool = True
    note: str
    source_ids: list[str] = []


@app.post("/api/review-queue/{rid}/confirmation")
def queue_confirmation(rid: str, payload: ReviewConfirmation, request: Request):
    authorize(request)
    queue = get_review_queue()
    queue.confirm(rid, payload.model_dump())
    return queue.detail(rid)


@app.post("/api/review-agents/control")
def agent_control(payload: AgentControl, request: Request):
    authorize(request)
    from datetime import datetime, timezone

    path = DATA / "review-agent-config.json"
    with LOCK:
        config = json.loads(path.read_text(encoding="utf8")) if path.exists() else {}
        if payload.enabled and not config.get("enabled"):
            config["activated_at"] = datetime.now(timezone.utc).isoformat()
        config.update(enabled=payload.enabled)
        config.setdefault("daily_limit", 20)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(config), encoding="utf8")
        temporary.replace(path)
    return get_review_queue().overview()


def attach_review_request(record):
    # Saving feedback succeeds even if the queue is unavailable; reconciliation repairs delivery.
    try:
        record["review_request_id"] = get_review_queue().enqueue(record["id"])
    except Exception:
        record["review_queue_error"] = (
            "원본과 메모는 저장됐지만 검토 요청 등록을 재시도해야 합니다."
        )
    return record


@app.get("/api/review-queue")
def queue_list():
    return get_review_queue().overview()


@app.get("/api/parser-repairs")
def repair_list():
    return get_repair_queue().overview()


class RepairControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    mode: Literal["local_refresh", "code_patch", "automatic"] = "local_refresh"


@app.post("/api/parser-repairs/control")
def repair_control(payload: RepairControl, request: Request):
    authorize(request)
    get_repair_queue().enable(payload.enabled, payload.mode)
    return get_repair_queue().overview()


@app.post("/api/parser-repairs/from-review/{rid}")
def repair_submit(rid: str, request: Request):
    authorize(request)
    try:
        ident = get_repair_queue().enqueue(rid)
    except ValueError:
        raise HTTPException(404, "검토 요청을 찾지 못했습니다.") from None
    return dict(id=ident)


@app.post('/api/parser-repairs/{ident}/retry')
def repair_retry(ident: str, request: Request):
    authorize(request)
    try:
        get_repair_queue().retry(ident)
    except ValueError:
        raise HTTPException(409, '실행 중이거나 재실행할 수 없는 요청입니다.') from None
    return dict(id=ident)


class RepairDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str


@app.post("/api/parser-repairs/datasets")
def repair_dataset(payload: RepairDataset, request: Request):
    authorize(request)
    if payload.job_id not in JOBS or JOBS[payload.job_id]["status"] != "done":
        raise HTTPException(404)
    pass
    pdf = DATA / payload.job_id / "input.pdf"
    path = DATA / "parser-repair-datasets.json"
    with LOCK:
        items = engine.read(path) if path.exists() else []
        if engine.sha(pdf) not in {i["sha256"] for i in items}:
            items.append(
                dict(
                    label=JOBS[payload.job_id]["name"],
                    path=str(pdf),
                    sha256=engine.sha(pdf),
                    role="evaluation",
                    independent_holdout_verified=False,
                )
            )
            engine.save(path, items)
    return dict(
        count=len(items),
        message="다음 코드 동결 시 평가 자료로 포함합니다. 기존 열람 여부를 확인하지 못했으므로 독립 Holdout으로 표시하지 않습니다.",
    )


@app.get("/api/parser-repairs/{ident}/diff")
def repair_diff(ident: str):
    item = next((i for i in get_repair_queue().overview()["items"] if i["id"] == ident), None)
    if not item:
        raise HTTPException(404)
    path = get_repair_queue().folder / ident / ("attempt-" + str(item["attempt"])) / "changes.diff"
    if not path.is_file():
        raise HTTPException(404)
    return Response(path.read_text(encoding="utf8"), media_type="text/plain")


@app.get('/api/parser-repairs/{ident}/reference')
def repair_reference(ident: str):
    item=next((i for i in get_repair_queue().overview()['items'] if i['id']==ident),None)
    if not item:
        raise HTTPException(404)
    path=get_repair_queue().folder/ident/('attempt-'+str(item['attempt']))/'reference.json'
    if not path.is_file():
        raise HTTPException(404)
    return Response(path.read_text(encoding='utf8'),media_type='application/json')


@app.post("/api/parser-repairs/{ident}/preview/{index}/{version}")
def repair_preview(ident: str, index: int, version: str, request: Request):
    authorize(request)
    item = next((i for i in get_repair_queue().overview()["items"] if i["id"] == ident), None)
    if not item or not item["result"] or version not in ("baseline", "candidate"):
        raise HTTPException(404)
    rows = item["result"]["datasets"]
    if not 0 <= index < len(rows) or not rows[index]["comparison"]:
        raise HTTPException(404)
    source = (
        get_repair_queue().folder
        / ident
        / ("attempt-" + str(item["attempt"]))
        / "outputs"
        / str(index)
        / version
    )
    jid = hashlib.sha256(
        (ident + str(item["attempt"]) + str(index) + version).encode()
    ).hexdigest()[:32]
    folder = DATA / jid
    with LOCK:
        if not folder.exists():
            import shutil

            folder.mkdir()
            for name in ("document.json", "result.json", "nested.json"):
                shutil.copy2(source / name, folder / name)
            shutil.copy2(rows[index]["dataset"]["path"], folder / "input.pdf")
            (folder / "review.json").write_text(
                json.dumps(
                    dict(
                        name="파서 실험 " + version + " · " + rows[index]["dataset"]["label"],
                        cases=[],
                    ),
                    ensure_ascii=False,
                ),
                encoding="utf8",
            )
        recover_jobs()
    return dict(job_id=jid)


@app.get("/api/review-queue/{rid}")
def queue_detail(rid: str):
    return get_review_queue().detail(rid)


@app.post("/api/review-queue/control")
def queue_control(payload: QueueControl, request: Request):
    authorize(request)
    get_review_queue().pause(payload.paused)
    return get_review_queue().overview()


@app.post("/api/review-queue/{rid}/action")
def queue_action(rid: str, payload: QueueAction, request: Request):
    authorize(request)
    get_review_queue().action(rid, payload.action)
    return get_review_queue().detail(rid)


@app.post("/api/error-basket/{item_id}/review-request")
def queue_submit(
    item_id: str,
    request: Request,
    mode: Literal["auto", "local", "agents", "agents_v2", "agents_roles"] = "auto",
):
    authorize(request)
    rid = get_review_queue().enqueue(item_id, mode=mode)
    if not rid:
        raise HTTPException(409, "제외한 사례는 복원한 후 검토할 수 있습니다.")
    return get_review_queue().detail(rid)


@app.get("/api/error-basket")
def basket_list():
    return error_basket.list_items(DATA / "error-basket")


@app.get("/api/error-basket/export")
def basket_export():
    return Response(
        error_basket.export_zip(DATA / "error-basket"),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="error-cases.zip"'},
    )


@app.get("/api/error-basket/{item_id}")
def basket_detail(item_id: str):
    return error_basket.detail(DATA / "error-basket", item_id)


def reference_source_folder(item_id):
    snapshot = error_basket.detail(DATA / "error-basket", item_id)["snapshot"]
    source = (DATA / snapshot["job_id"]).resolve()
    if source.parent != DATA.resolve():
        raise HTTPException(404)
    return source


@app.get("/api/error-basket/{item_id}/sources")
def basket_sources(item_id: str):
    return error_basket.reference_sources(
        DATA / "error-basket", item_id, reference_source_folder(item_id)
    )


@app.post("/api/error-basket/{item_id}/reference")
def basket_reference(item_id: str, payload: error_basket.ReferenceRequest, request: Request):
    authorize(request)
    return attach_review_request(
        error_basket.save_reference(
            DATA / "error-basket", item_id, reference_source_folder(item_id), payload
        )
    )


@app.get("/api/error-basket/{item_id}/image")
def basket_image(item_id: str):
    folder = error_basket.item_folder(DATA / "error-basket", item_id)
    return FileResponse(folder / "source.png", media_type="image/png")


@app.post("/api/jobs/{jid}/error-basket")
def basket_collect(jid: str, payload: error_basket.CollectionRequest, request: Request):
    authorize(request)
    if jid not in JOBS:
        raise HTTPException(404)
    return attach_review_request(
        error_basket.collect(DATA / "error-basket", DATA / jid, JOBS[jid], payload)
    )


@app.patch("/api/error-basket/{item_id}")
def basket_active(item_id: str, payload: error_basket.ActiveRequest, request: Request):
    authorize(request)
    return error_basket.set_active(DATA / "error-basket", item_id, payload.active)


@app.get("/api/samples")
def samples():
    return [dict(id=k, name=v[0]) for k, v in sample_catalog(ROOT).items()]


@app.get("/api/reviews")
def reviews():
    return [
        dict(id=j["id"], name=j["name"], pages=j["pages"], review=j["review"])
        for j in JOBS.values()
        if j.get("review")
    ]


@app.post("/api/sample/{sid}")
def sample(sid: str, request: Request):
    authorize(request)
    configured = sample_catalog(ROOT)
    if sid not in configured:
        raise HTTPException(404)
    name, path = configured[sid]
    return start(path.read_bytes(), name)


@app.post("/api/upload")
async def upload(request: Request):
    authorize(request)
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 20 * 1024 * 1024:
            raise HTTPException(413, "20MB 이하 파일을 선택해 주세요.")
    return start(bytes(data), Path(unquote(request.headers.get("x-file-name", "upload.pdf"))).name)


@app.get("/api/jobs/{jid}")
def status(jid: str):
    if jid not in JOBS:
        raise HTTPException(404)
    job = dict(JOBS[jid])
    progress_path = DATA / jid / "run-progress.json"
    if progress_path.is_file():
        try:
            job["progress"] = json.loads(progress_path.read_text(encoding="utf8"))
        except (OSError, ValueError):
            pass
    job["review_requests"] = document_review_requests(jid)
    return job


def document_review_requests(jid):
    """Read existing review requests; do not create or run reviews."""
    requests = []
    path = DATA / "review-queue" / "queue.sqlite3"
    if not path.is_file():
        return requests
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            for row in connection.execute(
                "SELECT id,status,stage,attempt,mode,payload FROM requests ORDER BY created_at DESC"
            ):
                payload = json.loads(row["payload"])
                if not isinstance(payload, dict) or not isinstance(payload.get("snapshot"), dict):
                    raise ValueError("invalid review payload")
                if payload.get("snapshot", {}).get("job_id") == jid:
                    requests.append({key: row[key] for key in ("id", "status", "stage", "attempt", "mode")})
    except (sqlite3.Error, ValueError, TypeError):
        return None
    return requests


@app.post("/api/jobs/{jid}/retry")
def retry_document(jid: str, request: Request):
    authorize(request)
    job = JOBS.get(jid)
    if not job:
        raise HTTPException(404)
    if job["status"] != "error":
        raise HTTPException(409, "실패가 확인된 실행만 새로 처리할 수 있습니다.")
    folder = DATA / jid
    try:
        provenance = json.loads((folder / "source.json").read_text(encoding="utf8"))
        pdf = (folder / "input.pdf").read_bytes()
        extension = provenance["source_type"]
        if extension not in ("pdf", "png", "jpeg"):
            raise ValueError("unsupported source")
        original = pdf if extension == "pdf" else (folder / ("original." + extension)).read_bytes()
        if (hashlib.sha256(pdf).hexdigest() != provenance["analysis_sha256"] or
                hashlib.sha256(original).hexdigest() != provenance["source_sha256"]):
            raise ValueError("source changed")
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(409, "원본 해시를 확인할 수 없어 다시 실행하지 않았습니다.") from None
    return start(original, job["name"], parent_run_id=jid)


@app.get("/api/document-runs")
def document_runs():
    return [
        {"id": job["id"], "name": job["name"], "status": job["status"],
         "pages": job["pages"], "recovered": job.get("recovered", False)}
        for job in JOBS.values()
    ]


@app.get("/api/jobs/{jid}/document-view/{number}")
def document_view(jid: str, number: int):
    if jid not in JOBS or JOBS[jid]["status"] != "done" or not 1 <= number <= JOBS[jid]["pages"]:
        raise HTTPException(404)
    from doc_understanding.review.presentation.document import page_view

    folder = DATA / jid
    return page_view(
        json.loads((folder / "document.json").read_text(encoding="utf8")),
        json.loads((folder / "nested.json").read_text(encoding="utf8")),
        number,
    )


@app.get("/api/jobs/{jid}/page-json/{number}")
def page_json(jid: str, number: int):
    if jid not in JOBS or JOBS[jid]["status"] != "done" or not 1 <= number <= JOBS[jid]["pages"]:
        raise HTTPException(404)
    doc = json.loads((DATA / jid / "document.json").read_text(encoding="utf8"))
    tables = [t for t in doc["physical_tables"] if t["page_number"] == number]
    ids = {t["table_id"] for t in tables}
    raw = []
    review = JOBS[jid].get("review")
    if review:
        for path in sorted(
            (Path(review["extraction_run"]) / "physical").glob(
                f"page-{number:03}-table-*-structured.json"
            )
        ):
            raw.append(json.loads(path.read_text(encoding="utf8")))
    return dict(
        document_id=doc["document_id"],
        page_number=number,
        notice="원본 파일에서 고른 페이지별 실제 IR. 화면용 별칭 JSON이 아닙니다. 전체 문서는 다운로드를 이용하세요.",
        physical_tables=tables,
        raw_structured_tables=raw,
        canonical_table_structure=[t for t in doc["table_structure"] if t["table_id"] in ids],
        native_tables=doc["document_ir"]["pages"][number - 1]["tables"],
        logical_table_links=[
            link for link in doc.get("logical_table_links", []) if link["target_table_id"] in ids
        ],
        linked_source_tables=[
            t
            for t in doc["physical_tables"]
            if t["table_id"]
            in {
                link["source_table_id"]
                for link in doc.get("logical_table_links", [])
                if link["target_table_id"] in ids
            }
        ],
    )


@app.get("/api/jobs/{jid}/page/{number}")
def page(jid: str, number: int):
    if jid not in JOBS or not 1 <= number <= JOBS[jid]["pages"]:
        raise HTTPException(404)
    path = DATA / jid / f"page-{number}.png"
    if not path.exists():
        with fitz.open(DATA / jid / "input.pdf") as d:
            d[number - 1].get_pixmap(matrix=fitz.Matrix(1.35, 1.35)).save(path)
    return FileResponse(path, media_type="image/png")


@app.get("/api/jobs/{jid}/download/{kind}")
def download(jid: str, kind: str):
    if jid not in JOBS or kind not in ("document", "nested", "review"):
        raise HTTPException(404)
    path = DATA / jid / (kind + ".json")
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, filename=kind + ".json", media_type="application/json")


@app.get("/api/jobs/{jid}/continuation/{asset}")
def continuation_report(jid: str, asset: str):
    """Read-only, generated review files; never executes the paid graph."""
    import re

    if jid not in JOBS or not (JOBS[jid].get("review") or {}).get("continuation_report"):
        raise HTTPException(404)
    if not re.fullmatch(
        r"(comparison(?:-reviewed)?\.html|summary\.json|scope-audit\.json|[CN]\d{2}\.json|page-\d+\.png)",
        asset,
    ):
        raise HTTPException(404)
    folder = (DATA / jid / "continuation").resolve()
    path = (folder / asset).resolve()
    if path.parent != folder or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)


def recover_jobs():
    for folder in DATA.iterdir():
        if not folder.is_dir() or len(folder.name) != 32 or folder.name in JOBS:
            continue
        try:
            info = json.loads((folder / "job-info.json").read_text(encoding="utf8"))
            if not isinstance(info, dict):
                info = {}
        except (OSError, ValueError):
            info = {}
        saved_name = info.get("name") if isinstance(info.get("name"), str) else "저장된 분석 문서"
        saved_pages = info.get("pages") if type(info.get("pages")) is int else 0
        parent_id = info.get("parent_run_id") if isinstance(info.get("parent_run_id"), str) else None
        progress_path = folder / "run-progress.json"
        if progress_path.exists():
            try:
                progress = json.loads(progress_path.read_text(encoding="utf8"))
            except (OSError, ValueError):
                progress = {}
            if progress.get("status") != "completed":
                failed = progress.get("status") == "failed"
                JOBS[folder.name] = dict(
                    id=folder.name, name=saved_name, pages=saved_pages,
                    status="error" if failed else "unknown", recovered=True, parent_run_id=parent_id,
                    error=("이전 실행에서 문서 처리가 실패했습니다. 상세 오류는 저장되지 않았습니다."
                           if failed else "이전 실행의 작업자 상태를 확인할 수 없습니다. 자동 재실행하지 않았습니다."),
                )
                continue
        if folder.is_dir() and (folder / "result.json").exists() and len(folder.name) == 32:
            result = json.loads((folder / "result.json").read_text(encoding="utf8"))
            review = (
                json.loads((folder / "review.json").read_text(encoding="utf8"))
                if (folder / "review.json").exists()
                else None
            )
            if review and (folder / "assessment.json").exists():
                assessments = json.loads((folder / "assessment.json").read_text(encoding="utf8"))
                for case in review["cases"]:
                    case["assessment"] = assessments.get(case["id"])
            JOBS[folder.name] = dict(
                id=folder.name,
                name=review["name"] if review else saved_name,
                pages=len(result["pages"]),
                status="done",
                result=result,
                seconds=0,
                recovered=True,
                parent_run_id=parent_id,
                review=review,
            )


app.include_router(rag_router(get_rag_service, lambda: JOBS, authorize, HERE, lambda: TOKEN))


def mapping_documents():
    """매핑 테스트셋이 가리키는 문서만 읽는다(코퍼스 전체를 메모리에 올리지 않는다)."""
    import json
    from doc_understanding.rag import repository
    from doc_understanding.services.corpus_rag import INDEX_VERSION, latest_runs
    wanted = set()
    for path in (ROOT / "config/evaluation").glob("mapping-*.json"):
        try:
            wanted.update(json.loads(path.read_text(encoding="utf8")).get("documents", {}).values())
        except (OSError, ValueError, AttributeError):
            continue
    return {r["document_id"]: repository.fetch_document(r["run_id"]) for r in latest_runs()
            if r["index_version"] == INDEX_VERSION and r["document_id"] in wanted}


def mapping_page_jobs(docs):
    """코퍼스 문서 → 그 문서를 적재한 웹 작업 ID(원본 쪽 이미지를 /api/jobs/{id}/page/{n}로 보여 줄 수 있을 때만)."""
    from doc_understanding.services.corpus_rag import CorpusService, run_documents
    documents = run_documents()
    out = {}
    for jid, stored in CorpusService(get_rag_service())._job_runs().items():
        document = documents.get(stored.get("run_id"))
        if document in docs and jid in JOBS and (DATA / jid / "input.pdf").exists():
            out.setdefault(document, jid)
    return out


SOURCE_FILES = {}  # (찾는 폴더들) → 원본 파일 찾기(내용 해시를 한 번만 계산해 둔다).


def source_files():
    """원본 파일 찾기: data/와 화면 작업 폴더에서 내용 해시가 문서 ID와 같은 PDF·HTML을 찾는다
    (화면 업로드, 스크립트·일괄 적재 문서 모두 같은 방법으로 찾는다)."""
    from doc_understanding.services.source_view import SourceFiles
    roots = (ROOT / "data", DATA)
    with LOCK:
        if roots not in SOURCE_FILES:
            SOURCE_FILES[roots] = SourceFiles(roots)
        return SOURCE_FILES[roots]


class CurrentSourceFiles:
    """요청 시점의 폴더 설정으로 원본을 찾는다(라우터는 한 번 만들어 두고 쓴다)."""

    def find(self, document_ids, names=()):
        return source_files().find(document_ids, names)


def mapping_page_sources(docs):
    """코퍼스 문서 → 원본 파일. 이름은 찾는 순서에만 쓴다(이름이 같아도 내용이 다르면 쓰지 않고, 이름이 달라도
    내용이 같으면 쓴다)."""
    return source_files().find(docs, {d.document_ir.source_name for d in docs.values() if d.document_ir.source_name})


app.include_router(mapping_router(authorize, HERE, lambda: TOKEN, mapping_documents,
                                  set_path=ROOT / "config/evaluation/mapping-dev-v1.json",
                                  page_jobs=mapping_page_jobs, page_sources=mapping_page_sources,
                                  page_cache=ROOT / "work/mapping-pages"))
# 검색·답변 화면(/search): 쪽 그림은 매핑 검토 화면과 같은 보관 폴더를 쓴다.
app.include_router(search_router(get_rag_service, lambda: JOBS, authorize, HERE, lambda: TOKEN, CurrentSourceFiles(),
                                 page_cache=ROOT / "work/mapping-pages", feedback_folder=ROOT / "work/feedback"))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        worker(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        recover_jobs()
        import uvicorn

        uvicorn.run(app, host="127.0.0.1", port=8766, access_log=False)


if __name__ == "__main__":
    main()
