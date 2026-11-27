"""Local error collection, separate from extraction, Gold approval and training."""

import hashlib
import io
import json
import re
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import fitz
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from doc_understanding.tools import evidence_context

LOCK = threading.Lock()
ErrorType = Literal[
    "table_missing",
    "nested_table",
    "cell_boundary",
    "source_text",
    "row_path",
    "column_path",
    "context",
    "other",
]


class CollectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(ge=1)
    scope: Literal["cell", "table", "case_region"]
    table_id: str | None = Field(default=None, max_length=1000)
    cell_id: str | None = Field(default=None, max_length=1200)
    case_id: str | None = Field(default=None, max_length=200)
    error_type: ErrorType
    note: str = Field(default="", max_length=4000)


class ActiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active: bool


Role = Literal["column_header", "row_header", "row_group", "body", "note", "unknown"]


class ReferenceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(min_length=1, max_length=1200)
    quote: str = Field(default="", max_length=16000)


class ReferenceClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal[
        "role",
        "row_header",
        "column_header",
        "row_group",
        "table_continuation",
        "nested_table",
        "context",
    ]
    certainty: Literal["proposed", "uncertain"]
    source_id: str | None = Field(default=None, max_length=1200)
    target_id: str | None = Field(default=None, max_length=1200)
    observed_role: Role = "unknown"
    expected_role: Role = "unknown"
    relation: Literal["link", "no_link"] = "link"
    evidence: list[ReferenceEvidence] = Field(default_factory=list, max_length=50)
    reason: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def check_completeness(self):
        if not self.reason.strip():
            raise ValueError("판단 이유 또는 불확실한 이유를 적어 주세요.")
        if self.certainty == "proposed":
            if not self.source_id or not self.evidence:
                raise ValueError("정답 참조에는 대상과 원본 근거가 필요합니다.")
            if self.kind == "role" and self.expected_role == "unknown":
                raise ValueError("역할을 모르겠으면 불확실로 저장해 주세요.")
            if self.kind != "role" and not self.target_id:
                raise ValueError("연결할 대상 또는 연결하면 안 되는 대상을 지정해 주세요.")
        if self.kind == "role" and self.target_id:
            raise ValueError("역할 판단에는 대상 셀 하나만 지정합니다.")
        if self.target_id and self.source_id == self.target_id:
            raise ValueError("자기 자신과 연결할 수 없습니다.")
        return self


class ReferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_revision: str | None = Field(default=None, max_length=32)
    claims: list[ReferenceClaim] = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=4000)


def read(path):
    return json.loads(path.read_text(encoding="utf8"))


def write_new(path, value):
    # Exclusive creation keeps snapshots and feedback history immutable.
    with path.open("x", encoding="utf8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def item_folder(root, item_id):
    if not re.fullmatch("[a-f0-9]{32}", item_id):
        raise HTTPException(404, "사례를 찾지 못했습니다.")
    folder = root / item_id
    if not (folder / "snapshot.json").exists():
        raise HTTPException(404, "사례를 찾지 못했습니다.")
    return folder


def detail(root, item_id):
    folder = item_folder(root, item_id)
    snapshot = read(folder / "snapshot.json")
    history = sorted((read(p) for p in folder.glob("event-*.json")), key=lambda e: e["at"])
    if not history:
        raise HTTPException(409, "사례 저장이 완료되지 않았습니다.")
    feedback = next(e for e in reversed(history) if e["action"] == "collect")
    supplements = [read(p) for p in sorted(folder.glob("evidence-supplement-*.json"))]
    references = [e["reference"] for e in history if e["action"] == "reference"]
    latest = references[-1] if references else None
    return dict(
        id=item_id,
        snapshot=snapshot,
        history=history,
        feedback=feedback,
        evidence_supplements=supplements,
        reference=latest,
        reference_history=references,
        active=history[-1]["active"],
        gold_status=latest["status"] if latest else "unreviewed",
        training_ready=False,
    )


def summary(record):
    snap = record["snapshot"]
    sel = snap["selection"]
    return dict(
        id=record["id"],
        job_id=snap["job_id"],
        document_name=snap["document_name"],
        page=sel["page"],
        scope=sel["scope"],
        table_id=sel["table_id"],
        cell_id=sel["cell_id"],
        case_id=sel["case_id"],
        text=(snap.get("selected_cell") or {}).get("original_text", "")[:160],
        error_type=record["feedback"]["error_type"],
        note=record["feedback"]["note"],
        active=record["active"],
        updated_at=record["history"][-1]["at"],
        gold_status=record["gold_status"],
        reference_revision=(record.get("reference") or {}).get("revision"),
        training_ready=False,
    )


def list_items(root):
    if not root.exists():
        return []
    items = []
    for path in root.glob("*/snapshot.json"):
        # An interrupted collection with no event is not a completed saved item.
        if any(path.parent.glob("event-*.json")):
            items.append(summary(detail(root, path.parent.name)))
    return sorted(items, key=lambda i: i["updated_at"], reverse=True)


def event(folder, action, **values):
    write_new(
        folder / ("event-" + uuid.uuid4().hex + ".json"),
        dict(at=datetime.now(timezone.utc).isoformat(), action=action, **values),
    )


def collect(root, source, job, request):
    if job.get("status") != "done" or request.page > job["pages"]:
        raise HTTPException(422, "완료된 문서의 페이지를 선택해 주세요.")
    doc_bytes = (source / "document.json").read_bytes()
    doc = json.loads(doc_bytes)
    tables = [t for t in doc["physical_tables"] if t["page_number"] == request.page]
    table = next((t for t in tables if t["table_id"] == request.table_id), None)
    cell = None
    case = None
    if request.scope == "case_region":
        case = next(
            (
                c
                for c in (job.get("review") or {}).get("cases", [])
                if c["id"] == request.case_id and c["page"] == request.page
            ),
            None,
        )
        if not case or request.cell_id or request.table_id:
            raise HTTPException(422, "선정된 원본 영역을 선택해 주세요.")
        box = case["review_bbox"]
        selected_tables = [
            t
            for t in tables
            if fitz.Rect([t["bounding_box"][k] for k in ("x0", "y0", "x1", "y1")]).intersects(
                fitz.Rect(box)
            )
        ]
    else:
        if not table:
            raise HTTPException(422, "현재 페이지의 실제 표를 선택해 주세요.")
        if request.scope == "cell":
            cell = next((c for c in table["cells"] if c["cell_id"] == request.cell_id), None)
            if not cell:
                raise HTTPException(422, "선택한 표 안의 실제 셀을 선택해 주세요.")
        elif request.cell_id:
            raise HTTPException(422, "표 전체를 담을 때는 셀 선택을 해제해 주세요.")
        bbox = cell["source_bbox"] if cell else table["bounding_box"]
        box = [bbox[k] for k in ("x0", "y0", "x1", "y1")]
        selected_tables = [table]
    pdf_path = source / "input.pdf"
    pdf_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    declared = (job.get("review") or {}).get("document", {}).get("sha256")
    if declared and declared.removeprefix("sha256:") != pdf_hash:
        raise HTTPException(409, "저장된 원본 해시가 달라졌습니다. 원본을 확인해 주세요.")
    selected_ids = {t["table_id"] for t in selected_tables}
    nested = read(source / "nested.json") if (source / "nested.json").exists() else {"cells": []}
    evidence = evidence_context.context_evidence(doc, nested, selected_ids)
    selection = dict(
        page=request.page,
        scope=request.scope,
        table_id=request.table_id,
        cell_id=request.cell_id,
        case_id=request.case_id if case else None,
        bbox=box,
    )
    fingerprint = json.dumps(
        dict(
            job=job["id"],
            pdf=pdf_hash,
            canonical=hashlib.sha256(doc_bytes).hexdigest(),
            selection=selection,
        ),
        sort_keys=True,
    )
    item_id = hashlib.sha256(fingerprint.encode()).hexdigest()[:32]
    folder = root / item_id
    with LOCK:
        if not (folder / "snapshot.json").exists():
            with fitz.open(pdf_path) as pdf:
                page = pdf[request.page - 1]
                clip = fitz.Rect(box)
                if clip.is_empty or clip.is_infinite or not page.rect.contains(clip):
                    raise HTTPException(422, "원본 페이지 안의 유효한 영역이 아닙니다.")
                png = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), clip=clip).tobytes("png")
                context = (
                    page.get_pixmap(
                        matrix=fitz.Matrix(1.2, 1.2),
                        clip=fitz.Rect(
                            [table["bounding_box"][k] for k in ("x0", "y0", "x1", "y1")]
                        ),
                    ).tobytes("png")
                    if cell
                    else None
                )
            raw = []
            run = (job.get("review") or {}).get("extraction_run")
            if run:
                for path in (Path(run) / "physical").glob(
                    f"page-{request.page:03}-table-*-structured.json"
                ):
                    value = read(path)
                    if value["table_id"] in selected_ids:
                        raw.append(value)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "source.png").write_bytes(png)
            if context:
                (folder / "table-context.png").write_bytes(context)
            write_new(
                folder / "snapshot.json",
                dict(
                    schema_version="error-collection/1.0",
                    job_id=job["id"],
                    document_id=doc["document_id"],
                    document_name=job["name"],
                    selection=selection,
                    source_pdf_sha256=pdf_hash,
                    canonical_sha256=hashlib.sha256(doc_bytes).hexdigest(),
                    selected_cell=cell,
                    physical_tables=selected_tables,
                    raw_structured_tables=raw,
                    canonical_table_structure=[
                        s for s in doc["table_structure"] if s["table_id"] in selected_ids
                    ],
                    **evidence,
                    source_case_id=case["id"] if case else request.case_id,
                    dataset_use="development_feedback",
                    gold_status="unreviewed",
                    training_ready=False,
                    evidence_scope="selected area; containing table retained as context; not whole-document Gold",
                ),
            )
        event(
            folder, "collect", active=True, error_type=request.error_type, note=request.note.strip()
        )
        return summary(detail(root, item_id))


def supplement_context(root, item_id, source):
    """Append recovered evidence to an old collection; never replace its snapshot."""
    folder = item_folder(root, item_id)
    snapshot = read(folder / "snapshot.json")
    raw = (source / "document.json").read_bytes()
    if (
        hashlib.sha256(raw).hexdigest() != snapshot["canonical_sha256"]
        or hashlib.sha256((source / "input.pdf").read_bytes()).hexdigest()
        != snapshot["source_pdf_sha256"]
    ):
        raise ValueError("SUPPLEMENT_SOURCE_HASH_MISMATCH")
    doc = json.loads(raw)
    nested = read(source / "nested.json")
    context = evidence_context.context_evidence(
        doc, nested, {t["table_id"] for t in snapshot["physical_tables"]}
    )
    payload = dict(
        schema_version="review-context-supplement/1.0",
        snapshot_sha256=hashlib.sha256((folder / "snapshot.json").read_bytes()).hexdigest(),
        canonical_sha256=snapshot["canonical_sha256"],
        nested_sha256=hashlib.sha256((source / "nested.json").read_bytes()).hexdigest(),
        **context,
    )
    key = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]
    path = folder / f"evidence-supplement-{key}.json"
    with LOCK:
        if not path.exists():
            write_new(path, payload)
    return path


def set_active(root, item_id, active):
    with LOCK:
        folder = item_folder(root, item_id)
        event(folder, "restore" if active else "exclude", active=active)
        return summary(detail(root, item_id))


def reference_sources(root, item_id, source):
    """Bind human annotations to the exact collected PDF and Canonical snapshot."""
    snapshot = read(item_folder(root, item_id) / "snapshot.json")
    if not (source / "document.json").exists() or not (source / "input.pdf").exists():
        raise HTTPException(409, "원본 파일을 찾지 못했습니다. 기존 기록은 보존되어 있습니다.")
    raw = (source / "document.json").read_bytes()
    if (
        hashlib.sha256(raw).hexdigest() != snapshot["canonical_sha256"]
        or hashlib.sha256((source / "input.pdf").read_bytes()).hexdigest()
        != snapshot["source_pdf_sha256"]
    ):
        raise HTTPException(
            409, "수집 당시 원본과 현재 파일이 다릅니다. 기존 기록은 변경하지 않았습니다."
        )
    doc = json.loads(raw)
    sources = []
    for t in doc["physical_tables"]:
        sources.append(
            dict(
                id=t["table_id"],
                kind="table",
                page=t["page_number"],
                text="표 전체",
                bbox=t["bounding_box"],
            )
        )
        for c in t["cells"]:
            sources.append(
                dict(
                    id=c["cell_id"],
                    kind="cell",
                    table_id=t["table_id"],
                    page=t["page_number"],
                    text=c.get("original_text") or "",
                    bbox=c["source_bbox"],
                    span_refs=c.get("span_refs", []),
                )
            )
    for p in doc.get("document_ir", {}).get("pages", []):
        for b in p.get("blocks", []):
            for s in b.get("spans", []):
                box = s.get("source", {}).get("bbox")
                if box:
                    sources.append(
                        dict(
                            id=s["span_id"],
                            kind="span",
                            page=p["page_number"],
                            text=s["text_raw"],
                            bbox=box,
                        )
                    )
    # A page anchor permits review of missing extraction without inventing a cell ID.
    with fitz.open(source / "input.pdf") as pdf:
        for n, p in enumerate(pdf, 1):
            sources.append(
                dict(
                    id=f"{doc['document_id']}/review-page/{n}",
                    kind="page",
                    page=n,
                    text="",
                    bbox=dict(x0=0, y0=0, x1=p.rect.width, y1=p.rect.height),
                )
            )
    return dict(
        job_id=snapshot["job_id"],
        document_name=snapshot["document_name"],
        sources=sources,
        canonical_sha256=snapshot["canonical_sha256"],
        source_pdf_sha256=snapshot["source_pdf_sha256"],
    )


def save_reference(root, item_id, source, request):
    catalog = reference_sources(root, item_id, source)
    registry = {s["id"]: s for s in catalog["sources"]}
    used = set()
    for claim in request.claims:
        for sid in (claim.source_id, claim.target_id):
            if sid:
                if sid not in registry:
                    raise HTTPException(422, "원본에 없는 대상 ID입니다.")
                used.add(sid)
        if (
            claim.kind == "role"
            and claim.source_id
            and registry[claim.source_id]["kind"] not in ("cell", "span")
        ):
            raise HTTPException(422, "역할 판단은 실제 셀 또는 원문 문장을 선택해 주세요.")
        for e in claim.evidence:
            if e.source_id not in registry:
                raise HTTPException(422, "원본에 없는 근거 ID입니다.")
            src = registry[e.source_id]
            used.add(e.source_id)
            quote = "".join(e.quote.split())
            original = "".join(src["text"].split())
            if (quote and quote not in original) or (
                not quote and original and src["kind"] in ("cell", "span")
            ):
                raise HTTPException(422, "인용문은 선택한 원본 문자열에 포함되어야 합니다.")
    with LOCK:
        current = detail(root, item_id)
        if (current["reference"] or {}).get("revision") != request.base_revision:
            raise HTTPException(
                409, "다른 창에서 정답 참조가 갱신됐습니다. 작성 내용을 복사한 뒤 다시 열어 주세요."
            )
        certainty = {c.certainty for c in request.claims}
        status = (
            "uncertain"
            if certainty == {"uncertain"}
            else "reference_draft"
            if certainty == {"proposed"}
            else "partial_reference"
        )
        reference = dict(
            schema_version="human-reference/1.0",
            revision=uuid.uuid4().hex,
            parent_revision=request.base_revision,
            status=status,
            **request.model_dump(exclude={"base_revision"}),
            source_records=[registry[sid] for sid in sorted(used)],
            source_pdf_sha256=catalog["source_pdf_sha256"],
            canonical_sha256=catalog["canonical_sha256"],
            training_ready=False,
            dataset_use="development_feedback",
            approval="not_adjudicated",
        )
        event(
            item_folder(root, item_id), "reference", active=current["active"], reference=reference
        )
        return detail(root, item_id)


def export_zip(root):
    output = io.BytesIO()
    with LOCK, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        items = [item for item in list_items(root) if item["active"]]
        archive.writestr(
            "manifest.json",
            json.dumps(
                dict(
                    schema_version="error-collection/1.0",
                    training_ready=False,
                    gold_status="not_adjudicated",
                    reference_status="per_item",
                    items=items,
                    notice="오류 자료와 미승인 정답 참조 이력입니다. 불확실 판단을 구분하고 검토·데이터 분리 후 별도 학습용 변환이 필요합니다.",
                ),
                ensure_ascii=False,
                indent=2,
            ),
        )
        for item in items:
            record = detail(root, item["id"])
            folder = root / item["id"]
            archive.writestr(
                item["id"] + "/record.json", json.dumps(record, ensure_ascii=False, indent=2)
            )
            for name in ("source.png", "table-context.png"):
                if (folder / name).exists():
                    archive.write(folder / name, item["id"] + "/" + name)
    return output.getvalue()
