"""역할: 검색·답변 화면(/search) API. 인용 근거의 원문(쪽 그림·HTML 원문), 표 격자, 앞뒤 본문을 보여 주고,
문서 처리 상태와 사용자 의견 기록을 제공한다.

질문·적재·문서 찾기는 기존 코퍼스 API(/api/rag/corpus/…)와 업로드 API(/api/upload)를 그대로 쓴다. 여기에는 원문 대조와
상태 요약, 의견 기록만 있다. 문서·색인은 읽기만 한다. 모델 호출 없음.
"""
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse

from doc_understanding.rag import catalog as document_catalog
from doc_understanding.rag.indexing.writer import INDEX_VERSION
from doc_understanding.services import feedback as feedback_service
from doc_understanding.services import source_view
from doc_understanding.services.web_rag import WebRagError, safe_error

RUN = r"^[A-Za-z0-9_.:-]{1,80}$"
ITEM = r"^[A-Za-z0-9_.:/-]{1,400}$"
JOB = r"^[a-f0-9]{32}$"
# 이 화면이 질문·적재에 쓰는 임베딩(search.js의 EMBEDDING과 같다). '검색 가능'은 이 임베딩이 다 있는지로 판정한다
# (다른 모델의 벡터가 있어도 이 화면의 질문에서는 쓰이지 않는다).
SCREEN_EMBEDDING = dict(provider="openai", model="text-embedding-3-small", dimensions=1536)


def router(get_service, get_jobs, authorize, static, get_token, source_files, page_cache, feedback_folder,
           feedback_ledger=None):
    """source_files: services.source_view.SourceFiles. page_cache: 쪽 그림 보관 폴더. feedback_folder: 의견 기록 폴더."""
    api = APIRouter()
    lock = threading.Lock()
    ledger = dict(ledger=feedback_ledger) if feedback_ledger else {}

    def corpus():
        from doc_understanding.services.corpus_rag import CorpusService
        return CorpusService(get_service())

    def invoke(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except HTTPException:
            raise
        except WebRagError as error:
            raise HTTPException(409, str(error)) from None
        except Exception as error:
            raise HTTPException(503, safe_error(error)) from None

    def located(run_id):
        """실행 → 목록표 줄과 원본 파일(없으면 None). 주소로 파일 경로를 받지 않는다."""
        row = invoke(document_catalog.lookup, [run_id]).get(run_id)
        if row is None:
            raise HTTPException(404, "DOCUMENT_NOT_FOUND")
        path = source_files.find([row["document_id"]], [row["source_name"]]).get(row["document_id"])
        return row, path

    # 화면 파일은 static/search/ 아래에 기능별로 나뉘어 있다(index.html, css/, js/ 모듈).
    screen = Path(static) / "search"

    @api.get("/search")
    def page():
        return HTMLResponse((screen / "index.html").read_text(encoding="utf8").replace("__TOKEN__", get_token()))

    @api.get("/search/assets/{kind}/{name}")
    def asset(kind: str, name: str):
        """화면의 CSS·JS 모듈. 정해진 두 폴더의 정해진 이름 형식만 내준다(경로 조작 방지)."""
        import re
        types = dict(css=("css", "text/css"), js=("js", "application/javascript"))
        if kind not in types or not re.fullmatch(r"[a-z][a-z0-9_-]{0,40}\." + types[kind][0], name):
            raise HTTPException(404, "ASSET_NOT_FOUND")
        path = screen / kind / name
        if not path.is_file():
            raise HTTPException(404, "ASSET_NOT_FOUND")
        # 고친 화면이 바로 보이도록 매번 변경 여부를 확인하게 한다(바뀌지 않았으면 본문은 다시 보내지 않는다).
        return FileResponse(path, media_type=types[kind][1], headers={"Cache-Control": "no-cache"})

    @api.get("/api/search/status")
    def status():
        """화면 머리에 보이는 요약: 지금 검색할 수 있는 문서 수와 실행 중인 작업. 개발 정보는 details에 따로 둔다."""
        stats = invoke(document_catalog.stats, INDEX_VERSION)
        state = invoke(corpus().state)
        return dict(documents=stats["documents"], pages=stats["pages"], stale_documents=stats["stale_documents"],
                    api_key_available=state["api_key_available"], busy_ingest=state["busy_ingest"],
                    busy_question=state["busy_question"],
                    details=dict(index_version=INDEX_VERSION, tables=stats["tables"], entries=stats["entries"],
                                 vectors=stats["vectors"]))

    @api.get("/api/search/source")
    def source(run_id: str = Query(pattern=RUN), page: int = Query(1, ge=1, le=100000)):
        """인용 근거가 가리키는 문서·쪽을 어떻게 보여 줄 수 있는지(쪽 그림 / HTML 원문 / 없음)와 좌표를 쓸 수 있는지."""
        row, path = located(run_id)
        kind = source_kind = source_view.source_kind(path)
        stored = invoke(source_view.stored_page, run_id, page)
        actual = None
        if kind == "pdf":
            try:
                actual = source_view.pdf_page_size(path, page)
            except Exception:  # 열 수 없는 파일은 원문 없음으로 다룬다.
                kind = None
        exact, reason = source_view.positions(kind, stored, actual)
        if source_kind == "pdf" and kind is None:
            reason = "SOURCE_UNREADABLE"
        base = f"run_id={run_id}"
        uri = row.get("source_uri") if str(row.get("source_uri") or "").startswith(("http://", "https://")) else None
        return dict(run_id=run_id, name=row["name"], pages=row["pages"], page=page, kind=kind,
                    media_type=row.get("source_media_type"), available=kind is not None,
                    page_found=stored is not None and (kind != "pdf" or actual is not None),
                    positions=exact, positions_reason=reason,
                    width=stored["width"] if stored else None, height=stored["height"] if stored else None,
                    image=f"/api/search/source/page?{base}&number={page}" if kind == "pdf" and actual else None,
                    html=f"/api/search/source/html?{base}" if kind == "html" else None, source_uri=uri,
                    searchable=bool(row.get("is_current")) and row["index_version"] == INDEX_VERSION)

    @api.get("/api/search/source/page")
    def source_page(run_id: str = Query(pattern=RUN), number: int = Query(ge=1, le=100000)):
        row, path = located(run_id)
        if source_view.source_kind(path) != "pdf":
            raise HTTPException(404, "PAGE_IMAGE_NOT_AVAILABLE")
        target = source_view.page_folder(page_cache, row["document_id"]) / f"page-{number}.png"
        try:
            image = source_view.render_page(path, number, target, lock)
        except Exception:
            raise HTTPException(404, "PAGE_IMAGE_NOT_AVAILABLE") from None
        if image is None:
            raise HTTPException(404, "PAGE_NOT_FOUND")
        return FileResponse(image, media_type="image/png")

    @api.get("/api/search/source/html")
    def source_html(run_id: str = Query(pattern=RUN)):
        """원문 HTML을 격리된 틀 안에서 보여 준다. 스크립트와 외부 요청은 막는다."""
        _, path = located(run_id)
        if source_view.source_kind(path) != "html":
            raise HTTPException(404, "SOURCE_NOT_HTML")
        return FileResponse(path, media_type="text/html", headers={
            "Content-Security-Policy": source_view.SOURCE_POLICY, "X-Content-Type-Options": "nosniff"})

    @api.get("/api/search/table")
    def table(run_id: str = Query(pattern=RUN), table_id: str = Query(pattern=ITEM)):
        grid = invoke(source_view.table_grid, run_id, table_id)
        if grid is None:
            raise HTTPException(404, "TABLE_NOT_FOUND")
        return grid

    @api.get("/api/search/context")
    def context(run_id: str = Query(pattern=RUN), content_id: str = Query(pattern=ITEM)):
        items = invoke(source_view.neighbours, run_id, content_id)
        if items is None:
            raise HTTPException(404, "CONTENT_NOT_FOUND")
        return dict(items=items)

    @api.get("/api/search/uploads")
    def uploads(ids: str = Query(max_length=3300)):
        """문서 작업들의 처리 상태: 분석(파싱)과 검색 준비(색인·임베딩)를 따로 알려 준다.

        search: waiting(분석이 끝나지 않음) · not_ingested(분석만 끝남) · ingesting(적재 중) · keyword_only(색인됨, 임베딩
        미완료 — 키워드로만 검색됨) · ready(색인·임베딩 완료 — 검색 가능) · stale(예전 색인) · failed(적재 실패)"""
        import re
        wanted = [i for i in dict.fromkeys(ids.split(",")) if re.fullmatch(JOB, i)][:100]
        jobs = get_jobs()
        known = {i: jobs[i] for i in wanted if i in jobs}
        service = corpus()
        states = {j["job_id"]: j for j in invoke(service.catalog, known)["jobs"]}
        runs = [s["corpus_run_id"] for s in states.values() if s.get("corpus_run_id")]
        rows = invoke(document_catalog.lookup, runs)
        from doc_understanding.services.corpus_rag import embedding_profiles
        vectors = {}
        for p in invoke(embedding_profiles, runs):
            if all(p.get(k) == v for k, v in SCREEN_EMBEDDING.items()):
                vectors[p["run_id"]] = max(vectors.get(p["run_id"], 0), p["vectors"])
        state_now = invoke(service.state)
        ingest = state_now.get("ingest") or {}
        running = ingest.get("status") in ("queued", "running")
        items = {i["job_id"]: i for i in ingest.get("items") or []}
        waiting = set((ingest.get("config") or {}).get("job_ids") or []) if running else set()
        out = []
        for jid in wanted:
            job = known.get(jid)
            if job is None:
                out.append(dict(job_id=jid, parsing="unknown", search="waiting"))
                continue
            entry = dict(job_id=jid, name=job.get("name"), pages=job.get("pages"), parsing=job.get("status"),
                         parsing_error=job.get("error") if job.get("status") in ("error", "unknown") else None)
            state, item = states.get(jid), items.get(jid)
            run = state.get("corpus_run_id") if state else None
            if job.get("status") != "done":
                search = "waiting"
            elif running and jid in waiting and (item is None or item.get("status") == "running"):
                search = "ingesting"
                entry["stage"] = item.get("stage") if item else "queued"
                if item and item.get("total"):
                    entry["progress"] = dict(total=item.get("total"), embedded=item.get("embedded"))
            elif state and state["state"] == "in_corpus":
                total = rows.get(run, {}).get("entries", 0)
                search = "ready" if total and vectors.get(run, 0) >= total else "keyword_only"
                entry.update(run_id=run, entries=total, vectors=vectors.get(run, 0))
            elif item and item.get("status") == "failed":
                search = "failed"
                entry["search_error"] = item.get("error")
            elif state and state["state"] in ("stale", "stored_not_latest"):
                search = state["state"]
            else:
                search = "not_ingested"
            out.append(dict(entry, search=search))
        # 적재 줄은 문서별 검색 준비와도 함께 쓴다. 그 작업이 도는 동안에도 '적재 중'으로 알린다.
        return dict(items=out, busy_ingest=running or bool(state_now.get("busy_ingest")))

    @api.post("/api/search/feedback", status_code=201)
    def feedback(payload: feedback_service.FeedbackRequest, request: Request):
        """답변에 대한 사용자 의견을 기록한다. 정답 데이터나 파서에는 반영하지 않는다(검토 전 의견으로만 남긴다)."""
        authorize(request)
        try:
            task = corpus().task(payload.request_id)
        except WebRagError:
            raise HTTPException(404, "TASK_NOT_FOUND") from None
        if task.get("action") != "question" or task.get("status") in ("queued", "running"):
            raise HTTPException(409, "ANSWER_NOT_FINISHED")
        try:
            item = feedback_service.record(task, payload, Path(feedback_folder), **ledger)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return dict(feedback_id=item["feedback_id"], at=item["at"], kinds=item["kinds"], review=item["review"])

    @api.get("/api/search/feedback")
    def feedback_list(request_id: str = Query(pattern=JOB)):
        return dict(items=[dict(feedback_id=i["feedback_id"], at=i["at"], kinds=i["kinds"], labels=i["labels"],
                                comment=i["opinion"]["comment"], evidence_ids=i["opinion"]["evidence_ids"],
                                review=i["review"])
                           for i in feedback_service.listed(request_id, **ledger)])

    return api
