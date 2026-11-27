"""Export implemented IR schema and explicitly draft future schemas."""
# ruff: noqa: E402 -- local source bootstrap for running without editable install
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from doc_understanding.domain.models import DocumentIR
from doc_understanding.domain.pipeline_contracts import CanonicalDocument
from doc_understanding.parser.contracts import PhysicalTableIR, StructuredTableIR
from doc_understanding.parser.structure.models import StructuredTableIR as ImplementedStructuredTableIR
from doc_understanding.parser.structure.models import VisionStructureRequest, VisionStructureProposal
from doc_understanding.export.models import CleanDocument
from tests.support import sample_ir

folder = ROOT / "schemas"
folder.mkdir(exist_ok=True)
for name, model in [("document-ir", DocumentIR), ("physical-table-ir", PhysicalTableIR),
                     ("structured-table-ir.draft", StructuredTableIR),
                     ("structured-table-ir", ImplementedStructuredTableIR),
                     ("vision-structure-request", VisionStructureRequest),
                     ("vision-structure-proposal", VisionStructureProposal),
                     ("canonical-document.draft", CanonicalDocument),
                     ("clean-document", CleanDocument)]:
    (folder / f"{name}.schema.json").write_text(
        json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2), encoding="utf-8")
examples = ROOT / "examples"
examples.mkdir(exist_ok=True)
(examples / "synthetic-document-ir.json").write_text(sample_ir().model_dump_json(indent=2), encoding="utf-8")
print("Exported implemented and legacy draft schemas and one SYNTHETIC IR example.")
