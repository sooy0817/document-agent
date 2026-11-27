# 역할: PDF 추출·물리 표·구조 후보·배치 산출물을 한 실행으로 연결한다. DB 저장과 RAG 답변은 별도 계층이다.
"""Whole-PDF extraction and table reuse, without model calls."""
import hashlib
import json
from pathlib import Path
from time import perf_counter

from doc_understanding.domain.models import DocumentIR
from doc_understanding.parser.physical.runner import evaluate_pdf
from doc_understanding.parser.physical.physical_models import PhysicalTableIR
from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer
from doc_understanding.parser.layout import project


# 산출물을 새 JSON 파일로만 저장한다. 같은 이름의 기존 결과는 덮어쓰지 않는다.
def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


# PDF 전체의 물리 표와 구조 후보를 만들고 배치·산출물 목록을 저장한다. 모델 호출과 DB 적재는 하지 않는다.
def prepare(pdf: Path, output: Path, *, boundary_profile="stroke_fill", nested_regions=True):
    start = perf_counter()
    before = hashlib.sha256(pdf.read_bytes()).hexdigest()
    run, summary = evaluate_pdf(pdf, output, focus_pages=(), all_pages=True,
                                label="development_staging_one_document",
                                boundary_profile=boundary_profile, nested_regions=nested_regions)
    ir_bytes = (run / "document-ir.json").read_bytes()
    ir = DocumentIR.model_validate_json(ir_bytes)
    links, artifacts = [], [
        ("document-ir", "DocumentIR", run / "document-ir.json", ir.model_dump(mode="json"), None)
    ]
    for record in summary["tables"]:
        path = run / record["json"]
        physical = PhysicalTableIR.model_validate_json(path.read_bytes())
        page = ir.pages[record["page"] - 1]
        structured = SemanticTableAnalyzer().analyze(physical, source_page=page)
        destination = path.with_name(path.stem + "-structured.json")
        write_json(destination, structured.model_dump(mode="json"))
        page = ir.pages[record["page"] - 1]
        for aid, kind, p, value in (
            (physical.table_id, "PhysicalTableIR", path, physical),
            (physical.table_id + ":structured", "StructuredTableIR", destination, structured),
        ):
            artifacts.append((aid, kind, p, value.model_dump(mode="json"), page.page_id))
            links.append(dict(source=page.page_id, target=aid, kind="table_artifact",
                              reason="Original physical page contains table artifact; semantics unreviewed."))
    layout = project(ir, ir_bytes, links)
    destination = run / "document-layout.json"
    write_json(destination, layout.model_dump(mode="json"))
    artifacts.append(("layout", "DocumentLayoutProjection", destination,
                      layout.model_dump(mode="json"), None))
    if hashlib.sha256(pdf.read_bytes()).hexdigest() != before:
        raise ValueError("SOURCE_CHANGED")
    manifest = {
        "source": str(pdf.resolve()), "source_sha256": before, "run_dir": str(run.resolve()),
        "seconds": perf_counter() - start, "status": layout.processing_status,
        "pages": len(ir.pages), "blocks": len(layout.blocks), "table_summary": summary,
        "artifacts": [{"id": a, "kind": k, "path": str(p.resolve()), "page_id": pid}
                      for a, k, p, _, pid in artifacts],
        "disk_bytes": sum(p.stat().st_size for p in run.rglob("*") if p.is_file()),
    }
    write_json(run / "staging-manifest.json", manifest)
    return run, manifest


# 실행 manifest와 원본·IR 해시를 확인하고 저장에 필요한 문서·배치·산출물을 읽는다.
def load(manifest_path):
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    run = Path(manifest["run_dir"])
    ir = DocumentIR.model_validate_json((run / "document-ir.json").read_bytes())
    from doc_understanding.parser.layout import DocumentLayoutProjection
    layout = DocumentLayoutProjection.model_validate_json((run / "document-layout.json").read_bytes())
    artifacts = [(a["id"], a["kind"], Path(a["path"]),
                  json.loads(Path(a["path"]).read_text(encoding="utf-8")), a["page_id"])
                 for a in manifest["artifacts"]]
    original = Path(manifest["source"])
    if hashlib.sha256(original.read_bytes()).hexdigest() != ir.source_sha256:
        raise ValueError("SOURCE_CHANGED")
    if hashlib.sha256((run / "document-ir.json").read_bytes()).hexdigest() != layout.document_ir_sha256:
        raise ValueError("IR_CHANGED")
    return manifest, ir, layout, artifacts


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--boundary-profile", choices=["stroke", "stroke_fill"], default="stroke_fill")
    parser.add_argument("--no-nested-regions", action="store_true")
    args = parser.parse_args()
    print(prepare(args.pdf, args.output, boundary_profile=args.boundary_profile,
                  nested_regions=not args.no_nested_regions)[0])
