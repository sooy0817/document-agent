"""Actual native-PDF tests. Explicit skips when PyMuPDF is unavailable.

No generated synthetic sample is an NH real-document regression result.
"""
import importlib.util
import tempfile
import unittest
from pathlib import Path

from doc_understanding.domain.contracts import ExtractionError, ExtractionLimits, ExtractionRequest
from doc_understanding.parser.orchestration.workflow import extract_ir

HAS_ENGINE = importlib.util.find_spec("pymupdf") is not None


def native_pdf(rotation=0, scan=False, pages=1, merged=False, cropped=False):
    import pymupdf
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=400, height=400)
        for y in [80, 120, 160, 200]:
            page.draw_line((40, y), (340, y))
        for x in [40, 140, 240, 340]:
            page.draw_line((x, 120 if merged and x == 140 else 80), (x, 200))
        for x, y, text in [(50, 105, "Item"), (150, 105, "2024"), (250, 105, "2025"),
                           (50, 145, "Deposit"), (150, 145, "0"), (250, 145, "-12"),
                           (50, 185, "Other"), (250, 185, "66,224")]:
            page.insert_text((x,y), text, fontsize=10)
        page.insert_text((40, 45), "Synthetic disclosure", fontsize=12)
        page.insert_text((40, 240), "Note: source values must remain unchanged.", fontsize=10)
        if cropped:
            page.set_cropbox(pymupdf.Rect(20,20,380,380))
        page.set_rotation(rotation)
    if scan:
        scan_doc = pymupdf.open()
        pixels = doc[0].get_pixmap().tobytes("png")
        page = scan_doc.new_page(width=400, height=400)
        page.insert_image(page.rect, stream=pixels)
        result = scan_doc.tobytes()
        scan_doc.close()
    else:
        result = doc.tobytes()
    doc.close()
    return result


@unittest.skipUnless(HAS_ENGINE, "PyMuPDF not installed: native extraction not executed")
class PDFIntegrationTests(unittest.TestCase):
    def extract(self, data, root, limits=None):
        from doc_understanding.parser.extraction.pymupdf_adapter import PyMuPDFExtractor
        return extract_ir(ExtractionRequest(data, "synthetic.pdf", limits=limits or ExtractionLimits()),
                          PyMuPDFExtractor(), root)

    def test_grid_raw_cells_notes_coordinates_and_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = native_pdf()
            ir = self.extract(data, root)
            self.assertEqual(len(ir.pages[0].tables), 1)
            table = ir.pages[0].tables[0]
            self.assertEqual(table.source.bbox.xyxy, (40,80,340,200))
            self.assertEqual([[next(c.text_raw for c in table.cells if c.cell_id == cid)
                               for cid in row] for row in table.detector_grid],
                             [["Item","2024","2025"],["Deposit","0","-12"],["Other","","66,224"]])
            self.assertTrue(all(c.source.page_number == 1 for c in table.cells))
            self.assertTrue(all(c.span_refs for c in table.cells if c.text_raw))
            self.assertTrue(any("Note:" in (b.text_raw or "") for b in ir.pages[0].blocks))
            run = root / str(ir.run.run_id)
            self.assertEqual((run / ir.source.path).read_bytes(), data)
            self.assertTrue((run / ir.pages[0].image.artifact.path).is_file())

    def test_stable_ids_across_extractions(self):
        with tempfile.TemporaryDirectory() as directory:
            data = native_pdf()
            a = self.extract(data, Path(directory))
            b = self.extract(data, Path(directory))
            self.assertEqual(a.pages, b.pages)
            self.assertNotEqual(a.run.run_id, b.run.run_id)

    def test_rotation_and_crop_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            ir = self.extract(native_pdf(rotation=90, cropped=True), Path(directory))
            page = ir.pages[0]
            self.assertEqual(page.original_rotation, 90)
            self.assertEqual((page.width,page.height), (360,360))
            self.assertEqual(page.tables[0].source.bbox.xyxy, (20,60,320,180))
            self.assertAlmostEqual(page.image.page_to_pixel[0], 120/72)

    def test_scan_requires_ocr_no_fabricated_table(self):
        with tempfile.TemporaryDirectory() as directory:
            ir = self.extract(native_pdf(scan=True), Path(directory))
            self.assertEqual(ir.pages[0].text_status, "ocr_required")
            self.assertEqual(ir.pages[0].tables, ())
            self.assertEqual(ir.processing_status, "review_required")

    def test_multipage_preserves_physical_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            ir = self.extract(native_pdf(pages=2), Path(directory))
            self.assertEqual([p.page_number for p in ir.pages], [1,2])
            self.assertNotEqual(ir.pages[0].tables[0].cells[0].cell_id,
                                ir.pages[1].tables[0].cells[0].cell_id)
            self.assertEqual(ir.pages[1].tables[0].cells[0].source.page_number, 2)

    def test_merged_slots_not_invented_as_blank(self):
        with tempfile.TemporaryDirectory() as directory:
            ir = self.extract(native_pdf(merged=True), Path(directory))
            table = ir.pages[0].tables[0]
            self.assertTrue(any(cid is None for row in table.detector_grid for cid in row))
            self.assertTrue(any(i.code == "GRID_SPANS_UNRESOLVED" for i in table.issues))

    def test_page_and_render_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            for data, limits, code in [(native_pdf(pages=2), ExtractionLimits(max_pages=1), "TOO_MANY_PAGES"),
                                       (native_pdf(), ExtractionLimits(max_pixels=10), "PAGE_PIXEL_LIMIT")]:
                with self.subTest(code=code), self.assertRaises(ExtractionError) as context:
                    self.extract(data, Path(directory), limits)
                self.assertEqual(context.exception.code, code)

    def test_corrupt_pdf(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ExtractionError) as context:
            self.extract(b"%PDF-corrupt", Path(directory))
        self.assertEqual(context.exception.code, "INVALID_PDF")

    def test_encrypted_pdf(self):
        import pymupdf
        with pymupdf.open(stream=native_pdf(), filetype="pdf") as doc:
            data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,
                               owner_pw="fixture-owner", user_pw="fixture-reader")
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ExtractionError) as context:
            self.extract(data, Path(directory))
        self.assertEqual(context.exception.code, "ENCRYPTED_PDF")
