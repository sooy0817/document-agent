from fastapi import FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from doc_understanding.config import artifact_root, extraction_limits
from doc_understanding.domain.contracts import ExtractionError, ExtractionRequest, PDFExtractor
from doc_understanding.domain.models import DocumentIR
from doc_understanding.parser.orchestration.workflow import extract_ir


def create_app(extractor: PDFExtractor | None = None, output_root=None) -> FastAPI:
    app = FastAPI(title="Document Understanding - Phase 1", version="0.1.0")
    limits = extraction_limits()

    @app.get("/health")
    def health():
        return {"status": "ok", "phase": 1, "financial_fact_publication": False}

    @app.post("/v1/ir/extract", response_model=DocumentIR)
    async def extract(request: Request):
        if request.headers.get("content-type", "").split(";")[0] != "application/pdf":
            raise HTTPException(415, "Content-Type must be application/pdf")
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limits.max_bytes:
                raise HTTPException(413, "FILE_TOO_LARGE")
            chunks.append(chunk)
        try:
            adapter = extractor
            if adapter is None:
                from doc_understanding.parser.extraction.pymupdf_adapter import PyMuPDFExtractor
                adapter = PyMuPDFExtractor()
            return await run_in_threadpool(extract_ir,
                                           ExtractionRequest(b"".join(chunks), "upload.pdf", limits=limits),
                                           adapter, output_root or artifact_root())
        except ImportError:
            raise HTTPException(503, "EXTRACTION_DEPENDENCY_MISSING") from None
        except ExtractionError as exc:
            raise HTTPException(422, exc.code) from None
        except Exception:
            raise HTTPException(500, "EXTRACTION_FAILED") from None

    return app


app = create_app()
