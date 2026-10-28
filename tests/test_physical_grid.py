"""End-to-end synthetic PDF -> DocumentIR -> reconstructed physical grid."""

import tempfile
import unittest
from pathlib import Path

from doc_understanding.domain.contracts import ExtractionRequest
from doc_understanding.domain.models import BBox
from doc_understanding.extraction.physical_evidence_adapter import raster_evidence
from doc_understanding.extraction.pymupdf_adapter import PyMuPDFExtractor
from doc_understanding.orchestrator.workflow import extract_ir
from doc_understanding.table_understanding.physical_grid import PhysicalGridReconstructor
from doc_understanding.table_understanding.physical_validator import PhysicalStructureValidator
from doc_understanding.table_understanding.row_alignment import CellToRowAlignment
from tests.physical_fixtures import fixture_pdf


class PhysicalGridTests(unittest.TestCase):
    def test_open_outer_edges_preserve_full_region_and_require_review(self):
        from doc_understanding.table_understanding.line_detector import LineDetector
        from doc_understanding.table_understanding.physical_models import GridPolicy

        _, ir, _ = self.analyze()
        page = ir.pages[0]
        # Remove only outer vertical strokes, retaining horizontal endpoints.
        blocks = tuple(b for b in page.blocks if not (
            b.kind == "drawing" and b.source.bbox.x0 == b.source.bbox.x1
            and b.source.bbox.x0 in (40, 440)
        ))
        page = page.model_copy(update={"blocks": blocks, "tables": ()})
        self.assertTrue(LineDetector(GridPolicy()).vector(page))
        result = PhysicalGridReconstructor().analyze_page(ir.document_id, page)
        table = result.tables[0]
        self.assertEqual((table.bounding_box.x0, table.bounding_box.x1), (40, 440))
        self.assertEqual(len(table.columns), 4)
        self.assertEqual(table.analysis_state, "partial")
        self.assertTrue(table.unresolved_boundaries)

    def analyze(self, kind="regular", rename=False, scan=False, mode="auto", hints=()):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        data, golden = fixture_pdf(kind, rename, scan)
        root = Path(directory.name)
        ir = extract_ir(ExtractionRequest(data, "synthetic.pdf"), PyMuPDFExtractor(), root)
        page = ir.pages[0]
        pixels = (
            raster_evidence(page, root / str(ir.run.run_id)) if scan or mode == "raster" else None
        )
        result = PhysicalGridReconstructor().analyze_page(ir.document_id, page, pixels, mode, hints)
        return result, ir, golden

    def assert_golden(self, kind):
        result, ir, (rows, cols, cells) = self.analyze(kind)
        self.assertEqual(len(result.tables), 1)
        table = result.tables[0]
        self.assertEqual(table.analysis_state, "validated", table.validation_issues)
        self.assertEqual((len(table.rows), len(table.columns)), (rows, cols))
        self.assertEqual(
            {(c.row_index, c.column_index, c.rowspan, c.colspan) for c in table.cells},
            {(c.row, c.column, c.rowspan, c.colspan) for c in cells},
        )
        self.assertTrue(all(c.source.page_number == 1 for c in table.cells))
        self.assertEqual(
            {c.cell_id for c in table.source_cells},
            {evidence.source_cell_id for evidence in table.source_links},
        )
        return table

    def test_regular_grid_and_blank(self):
        table = self.assert_golden("regular")
        blank = next(c for c in table.cells if (c.row_index, c.column_index) == (3, 3))
        self.assertEqual(blank.original_text, "")

    def test_multilevel_horizontal_merge(self):
        table = self.assert_golden("horizontal")
        merged = next(c for c in table.cells if (c.row_index, c.column_index) == (0, 1))
        self.assertEqual(merged.colspan, 3)

    def test_vertical_merge(self):
        self.assert_golden("vertical")

    def test_horizontal_separator_exists_only_in_some_columns(self):
        self.assert_golden("partial_horizontal")

    def test_nested_occupancy_and_named_regressions(self):
        table = self.assert_golden("nested")
        by_text = {c.original_text.replace("\n", "").replace(" ", ""): c for c in table.cells}
        self.assertTrue(
            CellToRowAlignment.row_range_contains(by_text["차입금"], by_text["일반차입금"])
        )
        self.assertTrue(
            CellToRowAlignment.row_range_contains(by_text["대출금"], by_text["외상매출금"])
        )
        self.assertFalse(CellToRowAlignment.row_range_contains(by_text["비유동"], by_text["기타"]))
        self.assertTrue(CellToRowAlignment.row_range_contains(by_text["무수익성"], by_text["기타"]))

    def test_labels_do_not_influence_grid(self):
        a, _, _ = self.analyze("nested")
        b, _, _ = self.analyze("nested", rename=True)

        def geometry(t):
            return [
                (c.source_bbox, c.row_index, c.column_index, c.rowspan, c.colspan, c.occupied_slots)
                for c in t.cells
            ]

        self.assertEqual(geometry(a.tables[0]), geometry(b.tables[0]))

    def test_parallel_regions_keep_separate_grids(self):
        result, _, _ = self.analyze("parallel")
        self.assertEqual(len(result.tables), 2)
        left, right = result.tables
        self.assertLess(left.bounding_box.x1, right.bounding_box.x0)
        self.assertTrue(all(t.analysis_state == "validated" for t in result.tables))

    def test_incomplete_separator_does_not_become_merged_cell(self):
        result, _, _ = self.analyze("incomplete")
        table = result.tables[0]
        self.assertEqual(table.analysis_state, "partial")
        self.assertTrue(table.unresolved_boundaries)
        uncertain = [c for c in table.cells if c.status == "unresolved"]
        self.assertTrue(uncertain)
        self.assertTrue(all(c.rowspan is None and c.colspan is None for c in uncertain))

    def test_borderless_hint_preserved_as_review_required(self):
        result, _, _ = self.analyze("borderless", hints=(BBox(x0=40, y0=60, x1=440, y1=220),))
        table = result.tables[0]
        self.assertEqual(table.analysis_state, "review_required")
        self.assertFalse(table.cells)

    def test_borderless_without_hint_not_hallucinated(self):
        result, _, _ = self.analyze("borderless")
        self.assertFalse(result.tables)
        self.assertTrue(any(i.code == "NO_GRID_REGION_DETECTED" for i in result.diagnostics))

    def test_image_only_grid_uses_pixels_without_fabricated_text(self):
        result, ir, _ = self.analyze("vertical", scan=True)
        self.assertEqual(ir.pages[0].text_status, "ocr_required")
        self.assertEqual(len(result.tables), 1)
        table = result.tables[0]
        self.assertEqual(table.analysis_state, "validated", table.validation_issues)
        self.assertTrue(all(c.original_text is None for c in table.cells))
        self.assertTrue(any(c.rowspan == 3 for c in table.cells))
        self.assertTrue(all(c.extraction_method == "raster_grid" for c in table.cells))

    def test_nested_scan_spans_match_vector_geometry(self):
        vector, _, _ = self.analyze("nested")
        scan, _, _ = self.analyze("nested",scan=True)
        def spans(table):
            return {(c.row_index,c.column_index,c.rowspan,c.colspan) for c in table.cells}
        self.assertEqual(scan.tables[0].analysis_state,"validated")
        self.assertEqual(spans(vector.tables[0]),spans(scan.tables[0]))

    def test_validator_rejects_overlapping_or_invented_cells(self):
        result, ir, _ = self.analyze()
        table = result.tables[0]
        changed = table.model_copy(update={"cells": table.cells + (table.cells[0],)})
        checked = PhysicalStructureValidator().validate(changed, ir.pages[0])
        self.assertEqual(checked.analysis_state, "invalid")
        self.assertTrue(any(i.code == "CELL_GRID_OVERLAP" for i in checked.validation_issues))

    def test_nonrectangular_missing_edges_remain_unresolved(self):
        result, _, _ = self.analyze("nonrectangular")
        table = result.tables[0]
        self.assertEqual(table.analysis_state, "partial")
        l_shape = next(c for c in table.cells if len(c.occupied_slots) == 3)
        self.assertEqual(l_shape.status, "unresolved")
        self.assertIsNone(l_shape.rowspan)

    def test_validator_rejects_falsified_boundary(self):
        result, ir, _ = self.analyze()
        table = result.tables[0]
        edge = table.boundaries[0].model_copy(update={"status": "absent"})
        changed = table.model_copy(update={"boundaries": (edge,) + table.boundaries[1:]})
        checked = PhysicalStructureValidator().validate(changed, ir.pages[0])
        self.assertEqual(checked.analysis_state, "invalid")
        self.assertTrue(any(i.code == "CELL_BOUNDARY_CONFLICT" for i in checked.validation_issues))

    def test_evaluator_emits_json_overlay_and_partitioned_counts(self):
        from doc_understanding.eval.physical import evaluate_pdf
        import json

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / "test.pdf"
            pdf.write_bytes(fixture_pdf("horizontal")[0])
            run, summary = evaluate_pdf(
                pdf, root / "output", focus_pages=(1,), label="synthetic_fixture"
            )
            self.assertEqual(summary["detected_table_regions"], 1)
            self.assertEqual(summary["automatic_structure_pass"], 1)
            self.assertFalse(summary["accuracy_proven"])
            self.assertTrue((run / summary["tables"][0]["overlay"]).is_file())
            output = json.loads((run / summary["tables"][0]["json"]).read_text(encoding="utf-8"))
            self.assertEqual(output["schema_version"], "physical-table-ir/2.1")
