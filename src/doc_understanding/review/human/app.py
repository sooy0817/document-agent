"""Loopback-only local review server: python -m doc_understanding.review.human.app."""

from pathlib import Path
import hashlib
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
import pymupdf
from doc_understanding.review.human.store import ReviewStore, ReviewInput, GateInput

ROOT = Path(__file__).resolve().parents[4]


def create_app(cases=None, output=None, *, precheck_path=None, source_catalog=None,
               meaning_cases=None, review_pack=None, guidance_path=None):
    store = ReviewStore(
        cases or ROOT / "data/human-review/cases",
        output or ROOT / "data/human-review/events",
    )
    app = FastAPI(title="Human Structure Review")
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )

    @app.middleware("http")
    async def origin_guard(request: Request, call_next):
        if request.method == "POST":
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return JSONResponse(
                    {"detail": "다른 사이트에서의 저장 요청은 차단됩니다."}, status_code=403
                )
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"detail": "JSON required"}, status_code=415)
        result = await call_next(request)
        result.headers["X-Content-Type-Options"] = "nosniff"
        result.headers["Cache-Control"] = "no-store"
        return result

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(KeyError)
    async def key_error(request, exc):
        return JSONResponse({"detail": "알 수 없는 사례"}, status_code=404)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return Path(__file__).with_name("review.html").read_text(encoding="utf-8")

    @app.get("/arithmetic", response_class=HTMLResponse)
    def arithmetic():
        return Path(__file__).with_name("simple.html").read_text(encoding="utf-8")

    @app.get("/advanced", response_class=HTMLResponse)
    def advanced():
        return Path(__file__).with_name("review.html").read_text(encoding="utf-8")

    @app.get("/api/precheck")
    def precheck():
        import json

        if precheck_path is None:
            return dict(legacy_checks=[], proposal_checks=[], legacy_counts={}, proposal_counts={},
                        semantic_truth='not_established', fact_published=False, gold_approval=False)
        return json.loads(Path(precheck_path).read_text(encoding="utf-8"))

    @app.get("/api/cases")
    def cases_index():
        return [
            dict(
                case_id=k,
                origin=(c := store.case(k))["canonical"]["proposal"]["origin"],
                revision=c["revision"],
                gates=c["gates"],
                relations=len(c["current_proposal"]["relations"]),
                status=c["latest_review"]["status"] if c["latest_review"] else "unreviewed",
            )
            for k in store.paths
        ]

    @app.get("/api/cases/{key}")
    def case(key):
        return store.case(key)

    @app.post("/api/cases/{key}/review")
    def review(key: str, body: ReviewInput):
        return store.review(key, body)

    @app.post("/api/cases/{key}/gate")
    def gate(key: str, body: GateInput):
        return store.gate(key, body)

    @app.get("/api/gold-candidates")
    def candidates():
        return dict(
            schema_version="human-gold-candidates/0.1",
            items=store.candidates(),
            fact_published=False,
        )

    @app.get("/api/cases/{key}/source/{page_number}")
    def source(key: str, page_number: int, table_crop: bool = False):
        can = store._input(key)
        if page_number not in {x.page for x in can.evidence.items}:
            raise HTTPException(404)
        # The loader allowlists canonical artifacts; never accept arbitrary user paths.
        artifact = next((x for x in can.evidence.artifacts if x.kind == "pdf"), None)
        if artifact:
            pdf = Path(artifact.uri)
            expected = artifact.sha256
        else:
            import json

            if source_catalog is None:
                raise HTTPException(404, "원본 PDF 근거 또는 명시적인 매핑이 필요합니다.")
            catalog = json.loads(Path(source_catalog).read_text(encoding="utf-8"))
            row = next((x for x in catalog if x["id"] == key), None)
            if not row:
                raise HTTPException(404, "원본 PDF 매핑 없음")
            pdf = ROOT / row["pdf_path"]
            expected = row["source_sha256"]
        if hashlib.sha256(pdf.read_bytes()).hexdigest() != expected:
            raise HTTPException(409, "PDF 해시 불일치")
        with pymupdf.open(pdf) as doc:
            page = doc[page_number - 1]
            clip = None
            if table_crop:
                boxes = [x.bbox for x in can.evidence.items if x.page == page_number]
                clip = (
                    pymupdf.Rect(
                        min(b[0] for b in boxes) - 5,
                        min(b[1] for b in boxes) - 28,
                        max(b[2] for b in boxes) + 5,
                        max(b[3] for b in boxes) + 5,
                    )
                    & page.rect
                )
            pix = page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), clip=clip)
            return Response(
                pix.tobytes("png"),
                media_type="image/png",
                headers={
                    "X-Page-Width": str(page.rect.width),
                    "X-Page-Height": str(page.rect.height),
                },
            )

    from doc_understanding.review.semantic_description.routes import attach_routes

    attach_routes(app, ROOT, (output / "semantic") if output else None,
                  cases=meaning_cases, review_pack=review_pack, guidance_path=guidance_path)
    return app


app = create_app()
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765)
