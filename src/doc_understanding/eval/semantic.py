"""Reusable semantic artifact runner; no document-specific rules or evaluation answers."""

import html
import json
from collections import Counter
from pathlib import Path
from uuid import uuid4

import pymupdf

from doc_understanding.table_understanding.physical_models import PhysicalTableIR
from doc_understanding.table_understanding.semantic import SemanticTableAnalyzer, vision_request
from doc_understanding.table_understanding.semantic_models import (
    StructuredTableIR,
    VisionStructureRequest,
    VisionStructureProposal,
)


def overlay(pdf, page_number, results):
    with pymupdf.open(pdf) as document:
        page = document[page_number - 1]
        page.set_rotation(0)
        shape = page.new_shape()
        for result in results:
            nodes = result.row_tree + result.column_tree
            index = {n.node_id: n for n in nodes}
            for n in nodes:
                color = (
                    (0.9, 0.15, 0.1)
                    if n.evidence_state == "unresolved"
                    else (0.65, 0.2, 0.85)
                    if n.axis == "row"
                    else (0, 0.5, 0.8)
                )
                box = pymupdf.Rect(n.bbox.xyxy)
                shape.draw_rect(box)
                shape.finish(color=color, width=0.8)
                shape.insert_text(
                    (box.x0 + 1, box.y0 + 5),
                    f"{'R' if n.axis == 'row' else 'C'}{n.row_range[0]}:{n.column_range[0]}",
                    fontsize=4,
                    color=color,
                )
                if n.parent_id in index:
                    p = index[n.parent_id].bbox
                    shape.draw_line(
                        ((p.x0 + p.x1) / 2, (p.y0 + p.y1) / 2),
                        ((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2),
                    )
                    shape.finish(color=color, width=0.6, dashes="[2 2]")
            for m in result.cell_mappings:
                if m.evidence_state == "unresolved":
                    shape.draw_rect(pymupdf.Rect(m.bbox.xyxy))
                    shape.finish(color=(1, 0.35, 0), width=1)
        shape.commit()
        return page.get_pixmap(dpi=150, alpha=False).tobytes("png")


def write_evaluation(physical_files, output_root, pdf=None, pages=()):
    run = Path(output_root) / str(uuid4())
    run.mkdir(parents=True)
    tables = [PhysicalTableIR.model_validate_json(Path(p).read_bytes()) for p in physical_files]
    selected = [t for t in tables if not pages or t.page_number in pages]
    analyzer = SemanticTableAnalyzer()
    results = [analyzer.analyze(t, tables) for t in selected]
    entries = []
    for i, (t, s) in enumerate(zip(selected, results)):
        name = f"page-{t.page_number:03d}-table-{i:03d}"
        (run / f"{name}.json").write_text(s.model_dump_json(indent=2), encoding="utf8")
        (run / f"{name}-vision-request.json").write_text(
            vision_request(t, s).model_dump_json(indent=2), encoding="utf8"
        )
        entries.append(
            {
                "table_id": t.table_id,
                "file": f"{name}.json",
                "page": t.page_number,
                "row_nodes": len(s.row_tree),
                "column_nodes": len(s.column_tree),
                "value_cells": len(s.cell_mappings),
                "mapping_states": dict(Counter(m.evidence_state for m in s.cell_mappings)),
                "ambiguities": dict(Counter(a.code for a in s.ambiguities)),
                "issues": dict(Counter(i.code for i in s.issues)),
            }
        )
    for name, model in (
        ("structured-table-ir", StructuredTableIR),
        ("vision-structure-request", VisionStructureRequest),
        ("vision-structure-proposal", VisionStructureProposal),
    ):
        (run / f"{name}.schema.json").write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2), encoding="utf8"
        )
    for number in sorted({t.page_number for t in selected}):
        if pdf:
            (run / f"page-{number:03d}-semantic.png").write_bytes(
                overlay(pdf, number, [s for s in results if s.page_number == number])
            )
    summary = {
        "tables": entries,
        "table_count": len(entries),
        "human_review": "pending",
        "automatic_checks_are_not_semantic_accuracy": True,
        "mapping_states": dict(Counter(m.evidence_state for s in results for m in s.cell_mappings)),
        "unseen_document_accuracy": None,
        "llm_calls": 0,
    }
    (run / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf8"
    )

    def esc(x):
        return html.escape(str(x))

    parts = [
        "<!doctype html><meta charset='utf-8'><title>Semantic structure review</title><style>body{font:16px system-ui;max-width:1200px;margin:30px auto;color:#172539}img{max-width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere}td,th{padding:8px;border-bottom:1px solid #ddd}summary{cursor:pointer}small{color:#674b00}</style>",
        "<h1>Phase 2-B · 구조 후보 검토</h1><p>사람의 독립 검토 대기. 모든 의미 관계는 추론 후보이며 자동 검증은 정확도 인증이 아닙니다.</p><p>보라: 행 라벨 · 파랑: 열 헤더 · 주황/빨강: 미해결 · 점선: 부모 후보 관계</p>",
    ]
    for entry, s in zip(entries, results):
        parts.append(
            f"<h2>Page {s.page_number} · {esc(s.table_id)}</h2><a href='{entry['file']}'>StructuredTableIR JSON</a>"
        )
        for title, nodes in (("Row Tree", s.row_tree), ("Column Tree", s.column_tree)):
            idx = {n.node_id: n for n in nodes}

            def chain(n):
                return (chain(idx[n.parent_id]) + " → " if n.parent_id in idx else "") + (
                    n.label_raw or "[empty]"
                )

            parts.append(
                f"<details open><summary>{title}</summary><table><tr><th>경로</th><th>점유 행/열</th><th>근거</th></tr>"
            )
            for n in nodes:
                parts.append(
                    f"<tr><td>{esc(chain(n))}</td><td>{n.row_range} / {n.column_range}</td><td>{n.physical_evidence} → {n.evidence_state}</td></tr>"
                )
            parts.append("</table></details>")
        parts.append(
            f"<details><summary>Cell paths ({len(s.cell_mappings)})</summary><pre>{esc(json.dumps([m.model_dump(mode='json') for m in s.cell_mappings], ensure_ascii=False, indent=2))}</pre></details>"
        )
        parts.append(
            f"<details><summary>Ambiguities ({len(s.ambiguities)})</summary><pre>{esc(json.dumps([a.model_dump(mode='json') for a in s.ambiguities], ensure_ascii=False, indent=2))}</pre></details>"
        )
    if pdf:
        for number in sorted({s.page_number for s in results}):
            parts.append(
                f"<h2>Page {number} overlay</h2><img src='page-{number:03d}-semantic.png' alt='page {number} semantic overlay'>"
            )
    (run / "index.html").write_text("\n".join(parts), encoding="utf8")
    return run, summary
