# 역할: OCR 설정과 결과의 형식.
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

BBox = tuple[float, float, float, float]  # x0, y0, x1, y1. 쪽의 왼쪽 위가 원점, 단위는 PDF 포인트


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class OcrSettings(Model):
    """설치마다 정하는 값. `provider`가 none이면 OCR을 하지 않는다."""
    provider: Literal["rapidocr", "none"]
    language: str = "korean"
    dpi: int = Field(default=200, ge=72, le=600)
    threads: int = Field(default=0, ge=0, description="0이면 엔진 기본값")
    device: Literal["cpu", "cuda"] = "cpu"
    det_model: str | None = Field(default=None, description="글자 위치 모델 파일(.onnx). 폐쇄망에서는 반드시 준다")
    rec_model: str | None = Field(default=None, description="글자 인식 모델 파일(.onnx). 폐쇄망에서는 반드시 준다")
    model_name: str | None = Field(default=None, description="결과에 적을 모델 이름. 없으면 rec_model 파일 이름")


class OcrLine(Model):
    text: str
    confidence: float = Field(ge=0, le=1)
    bbox: BBox


class OcrPage(Model):
    page_no: int = Field(ge=1)
    engine: str
    model: str
    dpi: int
    lines: list[OcrLine]
    mean_confidence: float | None
