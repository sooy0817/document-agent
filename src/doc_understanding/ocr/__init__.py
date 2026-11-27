# 역할: 글자가 없는 쪽(스캔본)의 그림에서 글자·위치·신뢰도를 읽어 별도 결과로 둔다. 기존 파서 결과는 바꾸지 않는다.
"""글자 없는 쪽의 OCR.

    settings = configured()                       # config/ocr.json, 없으면 OCR을 쓰지 않는다
    engine = load(settings)                       # 설치하는 쪽이 고른 엔진
    page = read_image(engine, image, page_no=3, dpi=200)      # 줄마다 글자·좌표(PDF 포인트)·신뢰도
    cells, loose, unsure = assign(page.lines, boxes)   # 칸마다 글자와 가장 낮은 신뢰도, 칸 밖의 줄, 칸을 못 정한 줄
    pages = read_run(run_dir, engine, dpi=200)    # 분석이 끝난 폴더에서 글자 없는 쪽만 읽어 run_dir/ocr/에 둔다

- 읽은 글자는 고치지 않는다. 비교할 때만 삼각형 모양 글자를 같게 본다(`same_text`).
- 쪽 전체를 한 번 읽고 줄을 칸에 배정한다. 칸을 하나씩 잘라 읽는 방식은 측정에서 더 나빴다(66% 대 89.5%).
- 엔진은 설치마다 고른다. 기본 구현은 ONNX로 도는 RapidOCR이고 `pip install rapidocr onnxruntime`이 있어야 한다.
  폐쇄망에서는 모델 파일을 미리 넣고 `det_model`·`rec_model` 경로를 준다(이 모듈은 내려받지 않는다).
"""
from doc_understanding.ocr.assign import assign, same_text
from doc_understanding.ocr.engine import configured, load, read_image
from doc_understanding.ocr.models import OcrLine, OcrPage, OcrSettings
from doc_understanding.ocr.pages import load_run, pending, read_run

__all__ = ["OcrLine", "OcrPage", "OcrSettings", "assign", "configured", "load", "load_run", "pending", "read_image", "read_run",
           "same_text"]
