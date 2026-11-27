# 역할: 실행 폴더에 저장된 페이지 이미지를 물리 선 탐지용 회색조 근거로 읽는다.
"""SDK-specific pixel adapter, kept outside the independent table package."""

from pathlib import Path

import pymupdf

from doc_understanding.parser.physical.line_detector import RasterEvidence


# 이미지 참조가 실행 폴더 안에 있는지 검사하고 회색조 픽셀과 원래 좌표 변환을 반환한다.
def raster_evidence(page, run_directory: Path) -> RasterEvidence:
    target = (run_directory / page.image.artifact.path).resolve()
    if not target.is_relative_to(run_directory.resolve()):
        raise ValueError("unsafe page image reference")
    pix = pymupdf.Pixmap(str(target))
    gray = pymupdf.Pixmap(pymupdf.csGRAY, pix)
    return RasterEvidence(gray.width, gray.height, gray.samples, page.image.page_to_pixel)
