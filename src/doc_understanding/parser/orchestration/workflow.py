# 역할: PDF 추출 실행을 만들고 어댑터 반환값·입력 해시를 검증해 산출물 또는 실패 기록을 저장한다.
from hashlib import sha256
from pathlib import Path

from doc_understanding import __version__
from doc_understanding.domain.contracts import ExtractionError, ExtractionRequest, PDFExtractor
from doc_understanding.domain.models import DocumentIR, ExtractionRun
from doc_understanding.storage.ingestion.artifacts import LocalArtifactStore


# 새 실행 폴더에서 PDFExtractor를 호출하고 DocumentIR 형식·실행 ID·원본 해시를 재검증한다.
# 실패 시 failure.json을 기록하고 예외를 다시 전달한다. 행열 의미 분석이나 RAG는 실행하지 않는다.
def extract_ir(request: ExtractionRequest, extractor: PDFExtractor, output_root: Path) -> DocumentIR:
    if len(request.pdf_bytes) > request.limits.max_bytes:
        raise ExtractionError("FILE_TOO_LARGE")
    if not request.pdf_bytes or b"%PDF-" not in request.pdf_bytes[:1024]:
        raise ExtractionError("INVALID_PDF")
    run = ExtractionRun(parser_version=__version__, engine=extractor.name,
                        engine_version=extractor.version)
    root = output_root / str(run.run_id)
    root.mkdir(parents=True, exist_ok=False)
    store = LocalArtifactStore(root)
    try:
        ir = extractor.extract(request, store, run)
        # Validate even adapters returning model_construct/model_copy objects.
        ir = DocumentIR.model_validate_json(ir.model_dump_json())
        if ir.run != run:
            raise ExtractionError("ADAPTER_RUN_MISMATCH")
        if ir.source_sha256 != sha256(request.pdf_bytes).hexdigest():
            raise ExtractionError("ADAPTER_SOURCE_MISMATCH")
        store.write("document-ir.json", ir.model_dump_json(indent=2).encode("utf-8"),
                    "application/json")
        return ir
    except Exception as exc:
        code = exc.code if isinstance(exc, ExtractionError) else "EXTRACTION_FAILED"
        store.write("failure.json", ('{"status":"failed","code":"' + code + '"}').encode(),
                    "application/json")
        raise
