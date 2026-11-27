# 역할: 구조 검토 판정의 계약. 결정·보류·해당 없음과 이유 코드, 근거 ID, 판정 출처를 담는다.
from typing import Literal

from pydantic import BaseModel, ConfigDict

REVIEW_VERSION = "structure-review/1"


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    table_id: str
    aspect: Literal["layout", "scope"]
    status: Literal["decided", "ambiguous", "not_applicable"]
    # decided: 근거 충분 / ambiguous: NO_EVIDENCE·CONFLICT 등 / not_applicable: 판정 대상 아님
    reason: str
    # layout: {"header_rows": 1, "stub_columns": 1} 또는 행별 라벨 깊이를 더한
    #         {"header_rows": 1, "stub_columns": 2, "row_stub_depths": [0, 1, 2, ...]},
    # scope: {"content_id": ..., "text_raw": ...}
    choice: dict | None = None
    supports: tuple[str, ...] = ()
    counters: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    source: Literal["rule", "model"] = "rule"
    candidates: tuple[dict, ...] = ()
