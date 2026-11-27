# 역할: 검토 실행 전에 원본 해시·셀·span·경로 참조를 검사한다. 정답률 평가나 승인 기능이 아니다.
"""Read-only evidence checks. No inference, parser execution, or fact approval."""

import hashlib
import json
import re
from pathlib import Path

VERSION = "local-evidence-checks/1"


def inspect(record, data, progress=lambda stage: None):
    snap = record["snapshot"]
    findings = []

    def add(code, message, ids=(), severity="review"):
        findings.append(dict(code=code, message=message, source_ids=list(ids), severity=severity))

    progress("source_integrity")
    jid = snap["job_id"]
    if not re.fullmatch("[a-f0-9]{32}", jid):
        raise ValueError("INVALID_SOURCE_JOB")
    folder = Path(data) / jid
    for name, key in [("input.pdf", "source_pdf_sha256"), ("document.json", "canonical_sha256")]:
        if not (folder / name).is_file():
            return result(
                [
                    dict(
                        code="SOURCE_MISSING",
                        message="수집 당시 원본 파일을 찾지 못했습니다.",
                        source_ids=[],
                        severity="blocked",
                    )
                ]
            )
        if hashlib.sha256((folder / name).read_bytes()).hexdigest() != snap[key]:
            return result(
                [
                    dict(
                        code="SOURCE_CHANGED",
                        message="현재 파일이 수집 당시 원본과 다릅니다. 기존 기록으로 판단을 보류합니다.",
                        source_ids=[],
                        severity="blocked",
                    )
                ]
            )
    doc = json.loads((folder / "document.json").read_text(encoding="utf8"))
    progress("structure_checks")
    tables = snap.get("physical_tables", [])
    selected = snap["selection"].get("cell_id")
    structures = {s["table_id"]: s for s in snap.get("canonical_table_structure", [])}
    pages = doc.get("document_ir", {}).get("pages", [])
    spans = {s["span_id"] for p in pages for b in p.get("blocks", []) for s in b.get("spans", [])}
    counts = dict(tables=len(tables), cells=0, nonblank_cells=0, both_paths_present=0)
    if not tables:
        add(
            "NO_TABLE_IN_SELECTION",
            "선택 영역에 저장된 물리 표가 없습니다. 원본에서 표 누락 여부를 확인해야 합니다.",
            severity="blocked",
        )
    for table in tables:
        tid = table["table_id"]
        structure = structures.get(tid, {})
        maps = {m["cell_id"]: m for m in structure.get("cell_mappings", [])}
        row_ids = {n["node_id"] for n in structure.get("row_tree", [])}
        col_ids = {n["node_id"] for n in structure.get("column_tree", [])}
        cells = [c for c in table["cells"] if not selected or c["cell_id"] == selected]
        missing, invalid, unresolved, nonblank_without_spans = [], [], [], []
        for cell in cells:
            cid = cell["cell_id"]
            counts["cells"] += 1
            nonblank = bool((cell.get("original_text") or "").strip())
            counts["nonblank_cells"] += int(nonblank)
            m = maps.get(cid)
            if m and m.get("row_path") and m.get("column_path"):
                counts["both_paths_present"] += 1
            # A header may legitimately have no value paths. Absence is not proof of error.
            if nonblank and (not m or not m.get("row_path") or not m.get("column_path")):
                missing.append(cid)
            if m and (
                set(m.get("row_path", [])) - row_ids or set(m.get("column_path", [])) - col_ids
            ):
                invalid.append(cid)
            if (
                cell.get("status") == "unresolved"
                or cell.get("rowspan") is None
                or cell.get("colspan") is None
            ):
                unresolved.append(cid)
            refs = cell.get("span_refs", [])
            if nonblank and not refs:
                nonblank_without_spans.append(cid)
            if pages and set(refs) - spans:
                add(
                    "SPAN_REFERENCE_MISSING",
                    "셀의 원문 span ID 일부가 DocumentIR에 없습니다.",
                    [cid],
                    "error",
                )
        if invalid:
            add(
                "PATH_REFERENCE_INVALID",
                "행·열 경로가 해당 Tree에 없는 노드 ID를 가리킵니다.",
                invalid,
                "error",
            )
        if missing:
            add(
                "AXIS_PATH_REVIEW",
                "비공란 셀 중 행·열 경로가 비어 있습니다. 헤더·설명 셀은 정상일 수 있어 원본 역할 대조가 필요합니다.",
                missing,
            )
        if unresolved:
            add(
                "PHYSICAL_UNRESOLVED",
                "미해결 셀 또는 병합 크기가 정해지지 않은 셀이 있습니다.",
                unresolved,
            )
        if nonblank_without_spans:
            add(
                "SOURCE_SPAN_UNAVAILABLE",
                "문자열에 연결된 원문 span이 없습니다. 원본 대조가 필요합니다.",
                nonblank_without_spans,
            )
        if not structure:
            add(
                "STRUCTURE_UNAVAILABLE",
                "선택한 표의 의미 구조가 수집 기록에 없습니다.",
                [tid],
                "blocked",
            )
    progress("context_checks")
    for identity in snap.get("nested_parent_identity", []):
        if identity.get("status") != "matched":
            add(
                "PARENT_IDENTITY_UNRESOLVED",
                "중첩표 부모의 원본/물리 셀 연결이 미확정입니다.",
                [identity["native_parent_cell_id"]],
            )
    error_type = record["feedback"]["error_type"]
    if error_type in ("nested_table", "context", "table_missing"):
        add(
            "VISUAL_SCOPE_REVIEW_REQUIRED",
            "이 요청은 원본의 표 범위·포함 관계·설명 적용 범위 대조가 필요합니다. 로컬 검사만으로 오류 여부를 확정하지 않습니다.",
            [t["table_id"] for t in tables],
        )
    if not pages:
        add(
            "DOCUMENT_IR_UNAVAILABLE",
            "현재 자료에는 전체 원문 span 대조에 필요한 DocumentIR이 없습니다.",
            severity="blocked",
        )
    progress("report_ready")
    return result(findings, counts)


def result(findings, counts=None):
    verdict = (
        "insufficient_evidence"
        if any(f["severity"] == "blocked" for f in findings)
        else "suspected_issue"
        if findings
        else "no_structural_issue_detected"
    )
    return dict(
        checker_version=VERSION,
        verdict=verdict,
        findings=findings,
        counts=counts or {},
        review_status="human_review_pending",
        semantic_verified=False,
        training_ready=False,
        api_calls=0,
        estimated_cost_usd=0,
        parser_modified=False,
        limitations=[
            "이미지 의미 판독과 전체 셀 정답 대조는 실행하지 않았습니다.",
            "경로가 채워졌거나 로컬 검사를 통과해도 의미 정확도를 승인하지 않습니다.",
        ],
    )
