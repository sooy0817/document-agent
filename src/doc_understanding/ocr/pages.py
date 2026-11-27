# 역할: 분석이 끝난 실행 폴더에서 글자가 없는 쪽만 골라 OCR로 읽고, 결과를 그 폴더의 ocr/ 아래에 따로 둔다.
"""기존 분석 결과(document-ir.json, 표 파일)는 읽기만 하고 바꾸지 않는다.

쪽 그림은 보관된 원본(source.pdf)에서 설정한 해상도로 새로 만든다. 분석이 남긴 쪽 그림은 해상도가 낮아
(120dpi) 측정한 조건(200dpi)과 다르기 때문이다. 쪽을 돌려 놓지 않은 좌표로 읽어 분석 결과의 좌표와 맞춘다.
"""
import json
from pathlib import Path

from doc_understanding.ocr.engine import read_image
from doc_understanding.ocr.models import OcrPage


def pending(run):
    """실행 폴더에서 OCR로 읽어야 하는 쪽 번호. 없으면 엔진을 만들 필요가 없다."""
    document = json.loads((Path(run) / "document-ir.json").read_bytes())
    return [page["page_number"] for page in document["pages"] if page["text_status"] == "ocr_required"]


def read_run(run, engine, *, dpi=200):
    """실행 폴더의 글자 없는 쪽을 읽는다. 결과는 {쪽 번호: OcrPage}이고, 읽은 그림과 함께 run/ocr/NNNN.png·json으로 남긴다."""
    import pymupdf

    run = Path(run)
    wanted = pending(run)
    pages = {}
    if not wanted:
        return pages
    out = run / "ocr"
    out.mkdir(exist_ok=True)
    with pymupdf.open(run / "source.pdf") as pdf:
        for number in wanted:
            page = pdf[number - 1]
            page.set_rotation(0)  # 분석과 같은 좌표계. 보관된 파일은 바꾸지 않는다
            image = out / f"{number:04d}.png"  # 무엇을 읽었는지 남긴다
            page.get_pixmap(dpi=dpi, alpha=False).save(image)
            result = read_image(engine, image, page_no=number, dpi=dpi, origin=(page.rect.x0, page.rect.y0))
            (out / f"{number:04d}.json").write_text(result.model_dump_json(indent=1), encoding="utf8")
            pages[number] = result
    return pages


def load_run(run):
    """이미 읽어 둔 결과를 다시 읽는다. 없으면 빈 dict."""
    return {page.page_no: page for page in (OcrPage.model_validate_json(path.read_bytes())
                                            for path in sorted((Path(run) / "ocr").glob("*.json")))}
