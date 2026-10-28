from hashlib import sha256
from pathlib import Path

from doc_understanding import __version__
from doc_understanding.domain.contracts import ExtractionError, ExtractionRequest, PDFExtractor
from doc_understanding.domain.models import DocumentIR, ExtractionRun
from doc_understanding.ingestion.artifacts import LocalArtifactStore


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
