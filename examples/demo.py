"""Local synthetic demo only. Never reads the original development data."""
import argparse
import json
from pathlib import Path
import tempfile
from doc_understanding.domain.contracts import ExtractionRequest
from doc_understanding.extraction.pymupdf_adapter import PyMuPDFExtractor
from doc_understanding.orchestrator.workflow import extract_ir
from doc_understanding.table_understanding.physical_grid import PhysicalGridReconstructor
from doc_understanding.table_understanding.semantic import SemanticTableAnalyzer

ROOT = Path(__file__).resolve().parent

def run(output):
    data = (ROOT / "synthetic.pdf").read_bytes()
    ir = extract_ir(ExtractionRequest(data, "synthetic.pdf"), PyMuPDFExtractor(), output)
    before = ir.model_dump_json()
    physical = PhysicalGridReconstructor().analyze_page(ir.document_id, ir.pages[0])
    structures = [SemanticTableAnalyzer().analyze(t) for t in physical.tables]
    # Physical reconstruction must not mutate original DocumentIR.
    for t, s in zip(physical.tables, structures):
        (output / "physical.json").write_text(t.model_dump_json(indent=2), encoding="utf-8")
        (output / "structured.json").write_text(s.model_dump_json(indent=2), encoding="utf-8")
    assert ir.model_dump_json() == before
    observed = dict(fixture="synthetic-horizontal-merge", pages=len(ir.pages),
        tables=len(physical.tables), physical_cells=sum(len(t.cells) for t in physical.tables),
        merged_spans=sorted([[c.row_index,c.column_index,c.rowspan,c.colspan]
            for t in physical.tables for c in t.cells if (c.rowspan or 0)>1 or (c.colspan or 0)>1]),
        source_text_preserved=all(c.model_dump() == next(x.model_dump() for x in ir.pages[0].tables[0].cells if x.cell_id==c.cell_id) for t in physical.tables for c in t.source_cells),
        facts_published=0)
    expected = json.loads((ROOT / "expected.json").read_text(encoding="utf-8"))
    assert observed == expected, (observed, expected)
    print(json.dumps(observed, indent=2))
    print("PASS: synthetic geometry contract only; not real-document semantic accuracy.")

if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, help="Optional new directory for IR outputs")
    args=parser.parse_args()
    if args.output:
        args.output.mkdir(parents=True,exist_ok=False)
        run(args.output)
    else:
        with tempfile.TemporaryDirectory(prefix="nh-synthetic-") as output:
            run(Path(output))
