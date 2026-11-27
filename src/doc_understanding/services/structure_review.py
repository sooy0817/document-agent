"""구조 분석 실행과 후보 산출물 저장을 조율한다. 검토 표시는 별도 모듈에 위임한다."""
import json
from collections import Counter
from pathlib import Path
from uuid import uuid4

from doc_understanding.parser.physical.physical_models import PhysicalTableIR
from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer, vision_request
from doc_understanding.parser.structure.models import (
    StructuredTableIR, VisionStructureRequest, VisionStructureProposal,
)
from doc_understanding.review.presentation.structure import overlay, render_structure_review


def write_structure_review(physical_files, output_root, pdf=None, pages=()):
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

    (run / "index.html").write_text(
        render_structure_review(entries, results, include_images=bool(pdf)), encoding="utf8"
    )
    return run, summary
