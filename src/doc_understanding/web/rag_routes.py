"""역할: 완료 문서에 한정한 RAG API를 제공하고 외부 호출·출처 범위를 검사한다."""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse

from doc_understanding.services.corpus_rag import CorpusQuestionRequest, IngestRequest
from doc_understanding.services.web_rag import (
    PrepareRequest,
    QuestionRequest,
    WebRagError,
    safe_error,
)


def router(get_service, get_jobs, authorize, static, get_token=lambda: ""):
    api = APIRouter()

    def require_job(jid):
        record = get_jobs().get(jid)
        if not record:
            raise HTTPException(404, "DOCUMENT_NOT_FOUND")
        if record.get("status") != "done":
            raise HTTPException(409, "DOCUMENT_NOT_READY")
        return get_service()

    def invoke(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except WebRagError as error:
            raise HTTPException(409, str(error)) from None
        except Exception as error:
            raise HTTPException(503, safe_error(error)) from None

    @api.get("/rag.js")
    def script():
        return FileResponse(static / "rag.js", media_type="application/javascript")

    @api.get("/rag.css")
    def style():
        return FileResponse(static / "rag.css", media_type="text/css")

    @api.get("/api/jobs/{jid}/rag")
    def state(jid: str):
        service = require_job(jid)
        return invoke(service.state, jid)

    @api.get("/api/jobs/{jid}/rag/entries")
    def entries(
        jid: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=50),
        kind: str | None = Query(None, max_length=40),
        page: int | None = Query(None, ge=1),
    ):
        service = require_job(jid)
        return invoke(service.entries, jid, offset=offset, limit=limit, kind=kind, page=page)

    @api.post("/api/jobs/{jid}/rag/prepare", status_code=202)
    def prepare(jid: str, payload: PrepareRequest, request: Request):
        authorize(request)
        service = require_job(jid)
        return invoke(service.start, jid, "prepare", payload)

    @api.post("/api/jobs/{jid}/rag/question", status_code=202)
    def question(jid: str, payload: QuestionRequest, request: Request):
        authorize(request)
        service = require_job(jid)
        return invoke(service.start, jid, "question", payload)

    # --- 저장된 문서 전체(코퍼스)로 질문하는 화면 ---

    def corpus():
        from doc_understanding.services.corpus_rag import CorpusService
        return CorpusService(get_service())

    @api.get("/ask")
    def ask_page():
        return HTMLResponse((static / "ask.html").read_text(encoding="utf8").replace("__TOKEN__", get_token()))

    @api.get("/ask.js")
    def ask_script():
        return FileResponse(static / "ask.js", media_type="application/javascript")

    @api.get("/api/rag/corpus")
    def corpus_catalog():
        return invoke(corpus().catalog, get_jobs())

    @api.get("/api/rag/corpus/documents")
    def corpus_documents(q: str | None = Query(None, max_length=200), limit: int = Query(20, ge=1, le=100),
                         offset: int = Query(0, ge=0), stale: bool = False):
        return invoke(corpus().documents, get_jobs(), q, limit, offset, stale)

    @api.get("/api/rag/corpus/state")
    def corpus_state():
        return invoke(corpus().state)

    @api.get("/api/rag/corpus/tasks/{request_id}")
    def corpus_task(request_id: str):
        try:
            return corpus().task(request_id)
        except WebRagError:
            raise HTTPException(404, "TASK_NOT_FOUND") from None

    @api.post("/api/rag/corpus/ingest", status_code=202)
    def corpus_ingest(payload: IngestRequest, request: Request):
        authorize(request)
        return invoke(corpus().start, "ingest", payload, get_jobs())

    @api.post("/api/rag/corpus/question", status_code=202)
    def corpus_question(payload: CorpusQuestionRequest, request: Request):
        authorize(request)
        return invoke(corpus().start, "question", payload, get_jobs())

    return api
