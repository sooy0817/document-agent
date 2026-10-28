"""SDK-specific pixel adapter, kept outside the independent table package."""

from pathlib import Path

import pymupdf

from doc_understanding.table_understanding.line_detector import RasterEvidence


def raster_evidence(page, run_directory: Path) -> RasterEvidence:
    target = (run_directory / page.image.artifact.path).resolve()
    if not target.is_relative_to(run_directory.resolve()):
        raise ValueError("unsafe page image reference")
    pix = pymupdf.Pixmap(str(target))
    gray = pymupdf.Pixmap(pymupdf.csGRAY, pix)
    return RasterEvidence(gray.width, gray.height, gray.samples, page.image.page_to_pixel)
