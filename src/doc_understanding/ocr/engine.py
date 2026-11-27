# 역할: 설정에 따라 OCR 엔진을 만들고, 쪽 그림 한 장을 읽어 PDF 좌표의 줄 목록으로 돌려준다.
"""설정 파일은 `config/ocr.json`(또는 환경변수 `NH_OCR_CONFIG`가 가리키는 파일)이다. 예:

    {"provider": "rapidocr", "language": "korean", "dpi": 200, "threads": 8,
     "det_model": "D:/models/ch_PP-OCRv5_det_mobile.onnx", "rec_model": "D:/models/korean_PP-OCRv5_rec_mobile.onnx"}
    {"provider": "none"}

엔진은 `read(image) -> [(네 꼭짓점, 글자, 신뢰도)]`만 가지면 된다. 꼭짓점은 그림의 화소 좌표다.
"""
import json
import os
from pathlib import Path

from doc_understanding.ocr.models import OcrLine, OcrPage, OcrSettings

ROOT = Path(__file__).resolve().parents[3]


def configured():
    """설정 파일의 내용. 파일이 없으면 None(OCR을 쓰지 않음)."""
    file = Path(os.environ.get("NH_OCR_CONFIG") or ROOT / "config" / "ocr.json")
    if not file.is_file():
        return None
    return OcrSettings.model_validate(json.loads(file.read_text(encoding="utf-8-sig")))


class RapidOcrEngine:
    name = "rapidocr"

    def __init__(self, settings):
        for label, value in (("det_model", settings.det_model), ("rec_model", settings.rec_model)):
            if value and not Path(value).is_file():
                raise ValueError(f"OCR_MODEL_FILE_MISSING: {label}")
        try:
            from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR
        except ImportError:
            raise ValueError("OCR_ENGINE_NOT_INSTALLED: rapidocr") from None
        try:
            language = LangRec[settings.language.upper()]
        except KeyError:
            raise ValueError("OCR_LANGUAGE_UNKNOWN") from None
        params = {"Det.ocr_version": OCRVersion.PPOCRV5, "Det.model_type": ModelType.MOBILE,
                  "Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.model_type": ModelType.MOBILE,
                  "Rec.lang_type": language, "Global.use_cls": False,
                  "EngineConfig.onnxruntime.use_cuda": settings.device == "cuda"}
        if settings.threads:
            params["EngineConfig.onnxruntime.intra_op_num_threads"] = settings.threads
        if settings.det_model:
            params["Det.model_path"] = settings.det_model
        if settings.rec_model:
            params["Rec.model_path"] = settings.rec_model
        self.model = settings.model_name or (Path(settings.rec_model).stem if settings.rec_model
                                             else f"{settings.language}_PP-OCRv5_mobile")
        self._engine = RapidOCR(params=params)

    def read(self, image):
        # 앞선 호출의 설정이 남지 않게 단계마다 명시한다(위치 찾기를 끈 호출 뒤에 켜지지 않던 일이 있었다).
        result = self._engine(image, use_det=True, use_cls=False, use_rec=True)
        if not getattr(result, "txts", None) or getattr(result, "boxes", None) is None:
            return []
        return [([(float(x), float(y)) for x, y in box], text, float(score))
                for box, text, score in zip(result.boxes, result.txts, result.scores)]


def load(settings):
    """설정에 맞는 엔진. 설정이 없거나 provider가 none이면 None."""
    if settings is None or settings.provider == "none":
        return None
    return RapidOcrEngine(settings)


def read_image(engine, image, *, page_no, dpi, origin=(0.0, 0.0)):
    """쪽 그림 한 장 → OcrPage. image는 파일 경로나 배열, origin은 그림 왼쪽 위의 PDF 좌표다.

    글자는 엔진이 읽은 그대로 둔다. 좌표만 화소에서 PDF 포인트로 바꾼다.
    """
    scale = 72 / dpi
    lines = []
    for points, text, confidence in engine.read(str(image) if isinstance(image, Path) else image):
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        lines.append(OcrLine(text=text, confidence=round(min(1.0, max(0.0, confidence)), 4), bbox=(
            round(origin[0] + min(xs) * scale, 2), round(origin[1] + min(ys) * scale, 2),
            round(origin[0] + max(xs) * scale, 2), round(origin[1] + max(ys) * scale, 2))))
    mean = round(sum(line.confidence for line in lines) / len(lines), 4) if lines else None
    return OcrPage(page_no=page_no, engine=engine.name, model=engine.model, dpi=dpi, lines=lines, mean_confidence=mean)
