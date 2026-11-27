# 역할: 셀 매핑 테스트셋 계약. 셀은 ID가 아니라 문서 별칭·표 위치(pNNNN/physicalNNN)·행열 번호와 원문 앞부분으로 고정한다.
"""매핑 테스트셋

- role: 그 셀이 표에서 하는 일. value(값), row_label(행 라벨), column_header(열 머리글), corner(머리글 모서리),
  ignore(빈 셀·장식 등 채점 제외).
- value 셀만 row_path·column_path(원문 라벨 목록, 바깥 → 안쪽)를 가진다. 빈 column_path는 '열 머리글이 없는 표'다.
- container: 셀 안이 하나의 내용(문장·표가 원본 순서로 놓인 작은 문서)인 칸. content에 원본 순서의 항목을 적는다.
  안쪽 표는 그 내용 안의 문맥이며, 무엇에 적용되는지는 정답으로 적지 않는다(문맥 해석은 질문 평가로 본다).
- 표의 parents: 그 표를 담은 칸들(바깥 → 안쪽). 담기지 않은 표는 빈 목록.
- reviewed·reviewer: 기대값을 확인했는가와 확인한 주체(human: 사람이 화면에서, claude: Claude가 원본 대조로).
  Claude 확인은 사람 검토가 아니다. reviewed_by_human은 모든 셀을 사람이 확인했을 때만 참이다.
"""
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Role = Literal["value", "row_label", "column_header", "corner", "container", "ignore"]
Pattern = Literal["regular_grid", "variable_stub_depth", "header_spans_stub", "single_row_box",
                  "key_value", "header_region_ambiguous", "path_collision", "nested_rows", "multi_level_columns",
                  "contains_content", "nested_in_cell"]


class ContentRef(BaseModel):
    """담는 칸 안의 항목 하나. 문장은 원문 앞부분(공백 제거 30자), 표는 표 위치."""
    model_config = ConfigDict(extra="forbid")
    kind: Literal["text", "table", "other"]
    text: str | None = None
    table: str | None = Field(default=None, pattern=r"^p\d{4}/physical\d{3}$")


class CellRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: str = Field(pattern=r"^p\d{4}/physical\d{3}$")
    r: int = Field(ge=0)
    c: int = Field(ge=0)


class ExpectedCell(BaseModel):
    model_config = ConfigDict(extra="forbid")
    r: int = Field(ge=0)
    c: int = Field(ge=0)
    text: str = ""  # 원문 앞부분(공백 제거 40자). 다른 파서 실행에서 같은 셀인지 확인한다.
    role: Role
    row_path: list[str] = []
    column_path: list[str] = []
    content: list[ContentRef] = []
    reviewed: bool = False
    reviewer: Literal["human", "claude"] | None = None
    note: str | None = None

    @field_validator("row_path", "column_path")
    @classmethod
    def _clean(cls, labels):
        # 줄바꿈·연속 공백만 한 칸으로 줄인다. 비교는 공백을 모두 무시한다(same_path).
        return [" ".join(x.split()) for x in labels]


class ExpectedTable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc: str
    table: str = Field(pattern=r"^p\d{4}/physical\d{3}$")
    patterns: list[Pattern] = []
    note: str | None = None
    parents: list[CellRef] = []
    cells: list[ExpectedCell]


class MappingSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    status: Literal["draft", "reviewed"]
    reviewed_by_human: bool
    draft_by: str = ""
    note: str = ""
    documents: dict[str, str]
    tables: list[ExpectedTable]


def compact(text, limit=40):
    out = re.sub(r"\s+", "", text or "")
    return out[:limit] if limit else out


def load(path):
    return MappingSet.model_validate_json(Path(path).read_text(encoding="utf-8"))


def save(data, path):
    """사람 검토 결과를 저장한다. 표 순서·셀 순서를 고정해 변경 내역(diff)을 읽기 쉽게 한다."""
    for t in data.tables:
        t.cells.sort(key=lambda x: (x.r, x.c))
    data.reviewed_by_human = bool(data.tables) and all(c.reviewed and c.reviewer == "human"
                                                       for t in data.tables for c in t.cells)
    # 셀 하나를 한 줄로 쓴다(600셀을 사람이 diff로 검토할 수 있게).
    head = data.model_dump(exclude={"tables"})
    lines = ["{"] + [f" {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}," for k, v in head.items()]
    lines.append(' "tables": [')
    for i, t in enumerate(data.tables):
        meta = t.model_dump(exclude={"cells"})
        lines.append("  {" + ", ".join(f"{json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items())
                     + ', "cells": [')
        cells = [json.dumps(c.model_dump(exclude_defaults=True), ensure_ascii=False) for c in t.cells]
        lines += [f"   {c}," for c in cells[:-1]] + [f"   {cells[-1]}" if cells else ""]
        lines.append("  ]}" + ("," if i < len(data.tables) - 1 else ""))
    lines += [" ]", "}"]
    text = "\n".join(lines) + "\n"
    MappingSet.model_validate_json(text)  # 다시 읽을 수 있는지 확인한다.
    Path(path).write_text(text, encoding="utf-8")


def content_refs(items):
    """셀 보기의 담는 칸 항목 → 테스트셋 항목(비교용)."""
    out = []
    for i in items:
        if i["kind"] == "text":
            out.append(ContentRef(kind="text", text=compact(i["text"], 30)))
        elif i["kind"] == "table":
            out.append(ContentRef(kind="table", table="/".join(i["table"].split("/")[-2:])))
        else:
            out.append(ContentRef(kind="other"))
    return out


def same_path(a, b):
    return [compact(x, None) for x in a] == [compact(x, None) for x in b]


def table_id(document_id, table):
    return f"{document_id}/{table}"
