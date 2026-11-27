"""역할: 셀 매핑 검토 화면 API. 코퍼스 표의 셀별 분석 결과(역할·행열 경로·근거 사용 여부·이유)와
매핑 테스트셋의 기대값을 함께 보여 주고, 사람이 확인·수정한 기대값을 테스트셋 파일에 저장한다.

분석 결과와 문서는 읽기만 한다. 저장하는 것은 테스트셋(기대값)뿐이다. 모델 호출 없음.
원본 대조: 화면에서 올린 문서는 그 작업의 쪽 이미지를, 그 밖의 문서는 원본 파일(PDF 쪽 그림, HTML 원문)을 보여 준다.
"""
import re
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from doc_understanding.evaluation.mapping import failures as mapping_failures
from doc_understanding.evaluation.mapping.dataset import ExpectedCell, load, save, table_id
from doc_understanding.evaluation.mapping.score import score_table, summarize
from doc_understanding.rag.evidence.inspect import containment, table_view
# 원문 종류·쪽 그림·HTML 격리 정책은 검색 화면과 함께 쓴다(services.source_view).
from doc_understanding.services.source_view import SOURCE_POLICY, page_folder, render_page, source_kind

DEFAULT_SET = Path("config/evaluation/mapping-dev-v1.json")
SET_NAME = re.compile(r"mapping-[a-z0-9-]{1,40}")


class TableReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc: str = Field(min_length=1, max_length=40)
    table: str = Field(pattern=r"^p\d{4}/physical\d{3}$")
    # 어느 테스트셋인가(config/evaluation의 mapping-*.json 이름). 없으면 기본 세트.
    set: str | None = Field(default=None, pattern=r"^mapping-[a-z0-9-]{1,40}$")
    note: str | None = Field(default=None, max_length=2000)
    cells: list[ExpectedCell] = Field(max_length=2000)


def router(authorize, static, get_token, load_documents, set_path=DEFAULT_SET, page_jobs=lambda docs: {},
           page_sources=lambda docs: {}, page_cache=None):
    """load_documents() → {document_id: CanonicalDocument}. page_jobs(docs) → {document_id: 원본 쪽 이미지 작업 ID}.
    page_sources(docs) → {document_id: 원본 파일 경로}(작업이 없는 문서의 원본을 보여 줄 때). page_cache: 쪽 그림 보관 폴더."""
    api = APIRouter()
    lock = threading.Lock()
    cache = {}

    def documents(reload=False):
        with lock:
            if reload or "docs" not in cache:
                cache["docs"] = load_documents()
                cache["containment"] = {}
                try:
                    cache["jobs"] = page_jobs(cache["docs"])
                except Exception:  # 쪽 이미지는 보조 정보다. 못 찾으면 이미지 없이 보여 준다.
                    cache["jobs"] = {}
                try:
                    cache["sources"] = {k: Path(v) for k, v in page_sources(cache["docs"]).items()}
                except Exception:
                    cache["sources"] = {}
            return cache["docs"], cache["jobs"]

    def source_of(set_name, doc):
        """테스트셋의 문서 이름 → 원본 파일. 주소로 파일 경로를 받지 않는다(테스트셋에 있는 문서만)."""
        data = load(path_of(set_name))
        if doc not in data.documents:
            raise HTTPException(404, "DOCUMENT_NOT_IN_SET")
        documents()
        path = cache["sources"].get(data.documents[doc])
        if path is None or not path.is_file():
            raise HTTPException(404, "SOURCE_NOT_AVAILABLE")
        return data.documents[doc], path

    def info(document):
        # 담는 칸 계산(중첩 연결)은 문서마다 한 번만 한다.
        with lock:
            store = cache.setdefault("containment", {})
            if document.document_id not in store:
                store[document.document_id] = containment(document)
            return store[document.document_id]

    def parent_check(expected, view):
        actual = [(p["table"].split("/", 1)[1], p["r"], p["c"]) for p in view["parents"]]
        return dict(expected=[p.model_dump() for p in expected.parents], correct=actual == [
            (p.table, p.r, p.c) for p in expected.parents], context_carried=view["context_carried"])

    def path_of(name):
        """테스트셋 이름 → 파일. 기본 세트와 같은 폴더의 mapping-*.json만 허용한다(경로 조작 방지: 이름 형식 검사)."""
        if not name or name == Path(set_path).stem:
            return Path(set_path)
        if not SET_NAME.fullmatch(name):
            raise HTTPException(404, "MAPPING_SET_NOT_FOUND")
        path = Path(set_path).parent / f"{name}.json"
        if not path.is_file():
            raise HTTPException(404, "MAPPING_SET_NOT_FOUND")
        return path

    def find(data, doc, table):
        expected = next((t for t in data.tables if t.doc == doc and t.table == table), None)
        if expected is None or doc not in data.documents:
            raise HTTPException(404, "TABLE_NOT_IN_SET")
        return expected

    @api.get("/mapping")
    def page():
        return HTMLResponse((static / "mapping.html").read_text(encoding="utf8").replace("__TOKEN__", get_token()))

    @api.get("/mapping.js")
    def script():
        return FileResponse(static / "mapping.js", media_type="application/javascript")

    @api.get("/api/mapping")
    def overview(reload: bool = False, set: str | None = Query(None, max_length=60)):
        data = load(path_of(set))
        docs, _ = documents(reload)
        tables, rows_all = [], []
        for t in data.tables:
            doc = docs.get(data.documents[t.doc])
            tid = table_id(data.documents[t.doc], t.table)
            item = dict(doc=t.doc, table=t.table, patterns=t.patterns, note=t.note, cells=len(t.cells),
                        reviewed=sum(c.reviewed for c in t.cells),
                        reviewed_by_human=sum(c.reviewed and c.reviewer == "human" for c in t.cells))
            if doc is not None and any(p.table_id == tid for p in doc.physical_tables):
                view = table_view(doc, tid, info(doc))
                rows, _ = score_table(t, view)
                rows_all += rows
                item.update(page=int(t.table[1:5]), summary=summarize(rows),
                            parents=parent_check(t, view) if t.parents else None)
            else:
                item.update(missing=True)
            tables.append(item)
        sets = sorted(p.stem for p in Path(set_path).parent.glob("mapping-*.json") if SET_NAME.fullmatch(p.stem))
        return dict(sets=sets, default_set=Path(set_path).stem,
                    name=data.name, status=data.status, reviewed_by_human=data.reviewed_by_human,
                    draft_by=data.draft_by, note=data.note, documents=sorted(data.documents),
                    summary=summarize(rows_all), tables=tables)

    @api.get("/api/mapping/failures")
    def failures(set: str | None = Query(None, max_length=60)):
        """현재 분석 결과가 기대값과 다른 셀·표 목록(실패 장부와 같은 기록). 저장하지 않고 그때그때 계산한다."""
        data = load(path_of(set))
        docs, _ = documents()
        views = {}
        for t in data.tables:
            doc = docs.get(data.documents[t.doc])
            tid = table_id(data.documents[t.doc], t.table)
            if doc is not None and any(p.table_id == tid for p in doc.physical_tables):
                views[tid] = table_view(doc, tid, info(doc))
        items = mapping_failures.mapping_failures(data, views)
        return dict(set=data.name, summary=mapping_failures.summarize(items), failures=items)

    @api.get("/api/mapping/table")
    def table(doc: str = Query(max_length=40), table: str = Query(pattern=r"^p\d{4}/physical\d{3}$"),
              set: str | None = Query(None, max_length=60)):
        data = load(path_of(set))
        expected = find(data, doc, table)
        docs, jobs = documents()
        document = docs.get(data.documents[doc])
        tid = table_id(data.documents[doc], table)
        if document is None or not any(p.table_id == tid for p in document.physical_tables):
            raise HTTPException(404, "TABLE_NOT_IN_CORPUS")
        view = table_view(document, tid, info(document))
        rows, blockers = score_table(expected, view)
        pg = next((p for p in document.document_ir.pages if p.page_number == view["page"]), None)
        return dict(view, doc=doc, table=table, patterns=expected.patterns, note=expected.note,
                    parent_check=parent_check(expected, view),
                    expected=[c.model_dump() for c in expected.cells], scores=rows, blockers=dict(blockers),
                    summary=summarize(rows), source_name=document.document_ir.source_name,
                    page_size=dict(width=pg.width, height=pg.height) if pg else None,
                    page_job=jobs.get(document.document_id),
                    page_source=source_kind(cache["sources"].get(document.document_id)),
                    source_uri=document.source_uri if str(document.source_uri or "").startswith(("http://", "https://")) else None)

    @api.get("/api/mapping/page")
    def page_image(doc: str = Query(max_length=40), number: int = Query(ge=1, le=10000),
                   set: str | None = Query(None, max_length=60)):
        """원본 PDF의 한 쪽을 그림으로(화면에서 올리지 않은 문서용). 한 번 그린 쪽은 보관한다."""
        document_id, source = source_of(set, doc)
        if source_kind(source) != "pdf" or page_cache is None:
            raise HTTPException(404, "PAGE_IMAGE_NOT_AVAILABLE")
        target = render_page(source, number, page_folder(page_cache, document_id) / f"page-{number}.png", lock)
        if target is None:
            raise HTTPException(404, "PAGE_NOT_FOUND")
        return FileResponse(target, media_type="image/png")

    @api.get("/api/mapping/source")
    def source_html(doc: str = Query(max_length=40), set: str | None = Query(None, max_length=60)):
        """원문 HTML을 격리된 틀 안에서 보여 준다. 스크립트와 외부 요청은 막는다."""
        _, source = source_of(set, doc)
        if source_kind(source) != "html":
            raise HTTPException(404, "SOURCE_NOT_HTML")
        return FileResponse(source, media_type="text/html", headers={
            "Content-Security-Policy": SOURCE_POLICY, "X-Content-Type-Options": "nosniff"})

    @api.put("/api/mapping/table")
    def review(payload: TableReview, request: Request):
        authorize(request)
        target = path_of(payload.set)
        with lock:
            data = load(target)
            expected = find(data, payload.doc, payload.table)
            known = {(c.r, c.c): c for c in expected.cells}
            for c in payload.cells:
                old = known.get((c.r, c.c))
                if old is None or old.text != c.text:
                    # 좌표·원문은 초안에서 정한다. 화면에서는 역할·경로·검토 여부·메모만 바꾼다.
                    raise HTTPException(422, f"CELL_NOT_IN_SET r{c.r}c{c.c}")
                if c.role != "value":
                    c.row_path, c.column_path = [], []
                # 담는 칸 안 항목은 파서 연결에서 온 초안이다. 화면에서는 맞음·틀림(메모)만 남긴다.
                c.content = old.content if c.role == "container" else []
                # 확인 주체: 화면에서 사람이 확인(reviewer=human)했거나 고친 셀은 사람 확인이다.
                # 바뀌지 않은 셀은 기존 주체(claude 등)를 유지한다. 화면에서 claude 확인을 새로 만들 수는 없다.
                same = c.model_dump(exclude={"reviewer"}) == old.model_dump(exclude={"reviewer"})
                if not c.reviewed:
                    c.reviewer = None
                elif c.reviewer == "human" or not same:
                    c.reviewer = "human"
                else:
                    c.reviewer = old.reviewer
            given = {(c.r, c.c): c for c in payload.cells}
            expected.cells = [given.get(k, v) for k, v in known.items()]
            expected.note = payload.note
            save(data, target)
        return dict(saved=len(payload.cells), reviewed=sum(c.reviewed for c in expected.cells),
                    cells=len(expected.cells))

    return api
