import json
import unittest

from pydantic import ValidationError

from doc_understanding.domain.models import BBox, CellIR, DocumentIR
from tests.support import sample_ir


class EvidenceContractTests(unittest.TestCase):
    def test_roundtrip_preserves_raw_glyphs_and_blanks(self):
        ir = sample_ir()
        result = DocumentIR.model_validate_json(ir.model_dump_json())
        self.assertEqual([c.text_raw for c in result.pages[0].tables[0].cells],
                         ["", "0", "-1", "△2", "-", "66,224"])
        self.assertTrue(all(c.rowspan is None for c in result.pages[0].tables[0].cells))
        self.assertEqual(result.validation_status, "not_validated")

    def invalid_edit(self, edit):
        data = sample_ir().model_dump(mode="json")
        edit(data)
        with self.assertRaises(ValidationError):
            DocumentIR.model_validate(data)

    def test_reject_wrong_document_id(self):
        self.invalid_edit(lambda d: d.update(document_id="wrong"))

    def test_reject_wrong_source_hash(self):
        self.invalid_edit(lambda d: d["source"].update(sha256="0"*64))

    def test_reject_wrong_cell_page(self):
        self.invalid_edit(lambda d: d["pages"][0]["tables"][0]["cells"][0]["source"].update(page_number=2))

    def test_reject_missing_span(self):
        self.invalid_edit(lambda d: d["pages"][0]["tables"][0]["cells"][0].update(span_refs=["missing"]))

    def test_reject_duplicate_id(self):
        self.invalid_edit(lambda d: d["pages"][0]["tables"][0]["cells"][1].update(cell_id="c0"))

    def test_reject_missing_grid_reference(self):
        self.invalid_edit(lambda d: d["pages"][0]["tables"][0]["detector_grid"][0].__setitem__(0, "missing"))

    def test_reject_page_gap(self):
        self.invalid_edit(lambda d: d["pages"][0].update(page_number=2))

    def test_reject_financial_validation_claim(self):
        self.invalid_edit(lambda d: d.update(validation_status="validated"))

    def test_reject_singular_transform(self):
        self.invalid_edit(lambda d: d["pages"][0]["image"].update(page_to_pixel=[0]*6))

    def test_bbox_invalid_and_nonfinite(self):
        for coords in [(2,0,1,1),(0,2,1,1),(0,0,float("inf"),1)]:
            with self.subTest(coords=coords), self.assertRaises(ValidationError):
                BBox(x0=coords[0], y0=coords[1], x1=coords[2], y1=coords[3])

    def test_immutable_nested_evidence(self):
        cell = sample_ir().pages[0].tables[0].cells[0]
        with self.assertRaises(ValidationError):
            cell.text_raw = "0"

    def test_blank_not_unreadable(self):
        data = sample_ir().pages[0].tables[0].cells[0].model_dump()
        data.update(text_raw=None, extraction_status="unreadable")
        self.assertIsNone(CellIR.model_validate(data).text_raw)
        data.update(extraction_status="blank")
        with self.assertRaises(ValidationError):
            CellIR.model_validate(data)

    def test_no_extra_llm_fields(self):
        self.invalid_edit(lambda d: d.update(financial_facts=[{"value": 123}]))

    def test_json_schema_serializable(self):
        schema = json.loads(json.dumps(DocumentIR.model_json_schema()))
        self.assertEqual(schema["properties"]["schema_version"]["const"], "document-ir/1.0")
