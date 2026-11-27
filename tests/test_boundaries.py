import ast
import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from doc_understanding.domain.pipeline_contracts import CanonicalDocument, StructurePatch
from doc_understanding.parser.contracts import StructuredTableIR


class FutureContractTests(unittest.TestCase):
    def test_table_package_does_not_import_templates_db_or_llm(self):
        folder = Path(__file__).parents[1] / "src/doc_understanding/parser"
        for path in folder.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    self.assertFalse(any(x in module for x in ["templates", "sqlalchemy", "openai", "pipeline_contracts"]))

    def test_patch_cannot_contain_generated_number(self):
        with self.assertRaises(ValidationError):
            StructurePatch(target_id="h1", operation="propose_parent", evidence_cell_ids=("c1",),
                           reason="candidate", value=100)

    def test_draft_schemas_generate(self):
        for model in (CanonicalDocument, StructuredTableIR):
            self.assertIsInstance(json.loads(json.dumps(model.model_json_schema())), dict)
        self.assertIn("table_structure", CanonicalDocument.model_fields)
