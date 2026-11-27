"""PDF를 본문과 표의 읽기 순서가 있는 tabledoc 문서로 변환한다."""
from .text import render_text as render_text

from pathlib import Path
from tempfile import TemporaryDirectory

from doc_understanding.export.clean import clean_document
from doc_understanding.export.models import CleanDocument
from doc_understanding.parser.assembly.document import build_from_run
from doc_understanding.parser.pipeline import prepare


def parse_pdf(pdf: str | Path, *, evidence_dir: str | Path | None = None, ocr_engine=None, ocr_dpi: int | None = None) -> dict:
    """Return one JSON-compatible tabledoc/0.5 document.

    Supply evidence_dir to retain the source copy and intermediate IRs in a
    new run directory. By default those files live only for this call.

    Pages without native text are read by OCR when an engine is given or
    config/ocr.json selects one; otherwise they are reported with
    text_source "none". OCR lines are prose items with a confidence.
    The configured engine is created only when the PDF has such a page, so
    a PDF with native text parses without the OCR dependency or models.
    """
    def parse_in(directory: Path) -> dict:
        from doc_understanding.ocr import configured, load, pending, read_run

        run, _ = prepare(Path(pdf), directory)
        read = None
        if pending(run):
            engine, dpi = ocr_engine, ocr_dpi
            if engine is None:
                settings = configured()
                engine, dpi = load(settings), dpi or (settings.dpi if settings else None)
            if engine is not None:
                read = read_run(run, engine, dpi=dpi or 200)
        document = clean_document(build_from_run(run), ocr=read)
        CleanDocument.model_validate(document)
        return document

    if evidence_dir is not None:
        return parse_in(Path(evidence_dir))
    with TemporaryDirectory(prefix="nh-parse-") as temporary:
        return parse_in(Path(temporary))
