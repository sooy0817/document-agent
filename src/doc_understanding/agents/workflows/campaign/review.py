"""Local structural triage of exported observations, not an accuracy judgment."""

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class Cell(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    state: str


class Table(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    page: int = Field(ge=1)
    state: str
    cells: list[Cell]


class Page(BaseModel):
    model_config = ConfigDict(extra="ignore")
    number: int = Field(ge=1)
    text_status: str


class Export(BaseModel):
    model_config = ConfigDict(extra="ignore")
    document_id: str
    tables: list[Table]
    pages: list[Page]


def inspect_result(folder):
    """Return source-anchored signals; a lack of signals does not prove accuracy."""
    raw = (Path(folder) / "result.json").read_bytes()
    result = Export.model_validate_json(raw)
    findings = []
    for page in result.pages:
        if page.text_status == "ocr_required":
            findings.append({"code": "OCR_REQUIRED", "page": page.number})
    for table in result.tables:
        if table.state != "validated":
            findings.append({"code": "STRUCTURE_NOT_VALIDATED", "page": table.page,
                             "table_id": table.id, "state": table.state})
        for cell in table.cells:
            if cell.state in ("unresolved", "ambiguous"):
                findings.append({"code": "CELL_PATH_UNCERTAIN", "page": table.page,
                                 "table_id": table.id, "cell_id": cell.id,
                                 "state": cell.state})
    return {"schema_version": "campaign-local-review/1", "document_id": result.document_id,
            "result_sha256": hashlib.sha256(raw).hexdigest(), "findings": findings,
            "accuracy_verified": False, "llm_review_performed": False}


def save_review(folder):
    result = inspect_result(folder)
    path = Path(folder) / "campaign-review.json"
    with path.open("x", encoding="utf8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    return result
