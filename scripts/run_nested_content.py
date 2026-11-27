"""Offline full PDF rerun; preserve old artifacts and emit a conditional sidecar.

Evaluation reference JSON is deliberately not accepted by this pipeline.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from doc_understanding.domain.contracts import ExtractionRequest
from doc_understanding.parser.extraction.pymupdf_adapter import PyMuPDFExtractor
from doc_understanding.parser.orchestration.workflow import extract_ir
from doc_understanding.parser.physical.physical_grid import PhysicalGridReconstructor
from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer
from doc_understanding.parser.assembly.document import assemble, validate_document
from doc_understanding.parser.assembly.nested_content import (
    build_nested_content, validate_links, resolve_cell_content,
)


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stable(value):
    # Per-run bookkeeping is excluded, never IDs, values, spans or geometry.
    if isinstance(value, dict):
        return {k: stable(v) for k, v in value.items()
                if k not in {'analysis_run_id', 'physical_ir_sha256', 'source_ir_sha256'}}
    if isinstance(value, list):
        return [stable(v) for v in value]
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=False)
    protected = {str(p.resolve()): sha(p) for p in args.baseline.rglob('*') if p.is_file()}
    protected[str(args.pdf.resolve())] = sha(args.pdf)
    dump(out/'protected-before.json', protected)
    code = {str(p): sha(p) for root in ('src/doc_understanding', 'scripts/run_nested_content.py')
            for p in ([Path(root)] if Path(root).is_file() else Path(root).rglob('*.py'))}
    dump(out/'code-manifest.json', code)
    start = perf_counter()
    ir = extract_ir(ExtractionRequest(args.pdf.read_bytes(), args.pdf.name),
                    PyMuPDFExtractor(), out/'native')
    native_seconds = perf_counter()-start
    print(json.dumps(dict(native_pages=len(ir.pages), native_seconds=native_seconds)), flush=True)
    physical, structured, failures = [], [], []
    reconstructor, semantic = PhysicalGridReconstructor(), SemanticTableAnalyzer()
    stage_start = perf_counter()
    for page in ir.pages:
        try:
            result = reconstructor.analyze_page(ir.document_id, page, mode='vector')
            tables = [semantic.analyze(t) for t in result.tables]
            dump(out/f'pages/p{page.page_number:03}-physical.json', result.model_dump(mode='json'))
            dump(out/f'pages/p{page.page_number:03}-structured.json',
                 [t.model_dump(mode='json') for t in tables])
            physical.extend(result.tables)
            structured.extend(tables)
        except Exception as exc:
            failures.append(dict(page=page.page_number, error_type=type(exc).__name__, error=str(exc)))
        if page.page_number % 10 == 0:
            print(json.dumps(dict(pages_processed=page.page_number, failures=len(failures))), flush=True)
    if failures:
        dump(out/'failures.json', failures)
        raise RuntimeError('PAGE_FAILURES_PRESERVED_NO_PARTIAL_SUCCESS')
    doc = assemble(ir, tuple(physical), tuple(structured))
    dump(out/'canonical-document.json', doc.model_dump(mode='json'))
    stage_seconds = perf_counter()-stage_start
    link_start = perf_counter()
    links = build_nested_content(doc)
    link_seconds = perf_counter()-link_start
    dump(out/'canonical-document.nested-content.json', links.model_dump(mode='json'))
    checks = validate_links(doc, links)
    for cell in links.cells:
        suffix = cell.parent_cell_id.split('/')[-2:]
        dump(out/f'cells/p{cell.page_number:03}-{"-".join(suffix)}.json',
             resolve_cell_content(doc, links, cell.parent_cell_id))
    old = json.loads((args.baseline/'canonical-document.json').read_text(encoding='utf8'))
    fresh = doc.model_dump(mode='json')
    comparisons = {k: stable(old[k]) == stable(fresh[k]) for k in
                   ('physical_tables', 'table_structure', 'contents', 'cell_mappings',
                    'row_hierarchy', 'column_hierarchy', 'sections')}
    old_pages, new_pages = old['document_ir']['pages'], fresh['document_ir']['pages']
    comparisons['native_blocks_and_tables'] = (
        [(p['blocks'], p['tables']) for p in old_pages]
        == [(p['blocks'], p['tables']) for p in new_pages])
    changed = [p for p, digest in protected.items() if sha(Path(p)) != digest]
    summary = dict(pages=len(ir.pages), native_tables=sum(len(p.tables) for p in ir.pages),
                   physical_tables=len(physical), physical_cells=sum(len(t.cells) for t in physical),
                   structured_tables=len(structured), mappings=len(doc.cell_mappings),
                   candidate_count=len(links.candidates),
                   pages_routed=[p.page_number for p in links.pages if p.candidate_count],
                   pages_skipped=sum(not p.candidate_count for p in links.pages),
                   cells=[dict(page=c.page_number, parent=c.parent_cell_id, status=c.status,
                               item_count=len(c.items), reasons=c.reasons,
                               missing_spans=len(c.uncovered_span_ids)) for c in links.cells],
                   checks=checks, canonical_checks=validate_document(doc),
                   baseline_comparison=comparisons, protected_files=len(protected),
                   protected_files_changed=changed, native_seconds=native_seconds,
                   physical_semantic_assembly_seconds=stage_seconds,
                   conditional_link_seconds=link_seconds, elapsed_seconds=perf_counter()-start,
                   api_calls=0, db_writes=0, semantics_validated=False)
    dump(out/'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
