"""Independent checks on geometry, occupancy and source links, never financial correctness."""

from doc_understanding.domain.models import Issue
from .geometry import close_box
from .row_alignment import CellToRowAlignment


class PhysicalStructureValidator:
    def validate(self, table, page):
        issues = list(table.validation_issues)

        def issue(code, message, severity="error"):
            issues.append(
                Issue(
                    code=code,
                    severity=severity,
                    message=message,
                    source_references=table.source_references,
                )
            )

        if not table.rows or not table.columns:
            issue(
                "GRID_NOT_OBSERVED",
                "No supported row/column lattice; no spans inferred.",
                "warning",
            )
        for bands in [table.rows, table.columns]:
            if any(b.end <= b.start or b.index != i for i, b in enumerate(bands)):
                issue("INVALID_GRID_BANDS", "Invalid grid intervals.")
            if any(abs(a.end - b.start) > table.policy.tolerance for a, b in zip(bands, bands[1:])):
                issue("GRID_BAND_GAP", "Grid intervals are not contiguous.")
        sources = {c.cell_id: c for c in table.source_cells}
        spans = {s.span_id for b in page.blocks for s in b.spans}
        original = {c.cell_id for t in page.tables for c in t.cells}
        if not set(sources) <= original:
            issue("UNKNOWN_SOURCE_CELL", "Physical source cells not found on original page.")
        occupancy = {}
        ids = set()
        edges = {(b.axis, b.row, b.column): b for b in table.boundaries}
        line_ids = {evidence.line_id for evidence in table.lines}
        if any(not set(b.line_ids) <= line_ids for b in table.boundaries):
            issue("BOUNDARY_SOURCE_MISSING", "Boundary refers to unknown line evidence.")
        for cell in table.cells:
            cb, tb = cell.source_bbox, table.bounding_box
            if (
                cb.x0 < tb.x0 - table.policy.tolerance
                or cb.y0 < tb.y0 - table.policy.tolerance
                or cb.x1 > tb.x1 + table.policy.tolerance
                or cb.y1 > tb.y1 + table.policy.tolerance
            ):
                issue("CELL_OUTSIDE_TABLE", "Reconstructed bbox escapes its table region.")
            if cell.cell_id in ids:
                issue("DUPLICATE_CELL_ID", "Reconstructed cell ID repeated.")
            ids.add(cell.cell_id)
            if (
                cell.source.document_id != table.document_id
                or cell.source.page_number != table.page_number
            ):
                issue("SOURCE_PAGE_CONFLICT", "Cell source points to another page/document.")
            if not set(cell.source_cell_ids) <= set(sources) or not set(cell.span_refs) <= spans:
                issue("SOURCE_REF_MISSING", "Unknown cell/span in reconstruction.")
            for slot in cell.occupied_slots:
                if slot in occupancy:
                    issue("CELL_GRID_OVERLAP", "Elementary slot occupied by multiple cells.")
                occupancy[slot] = cell.cell_id
            if cell.status in ("resolved", "inferred"):
                if cell.rowspan is None or cell.colspan is None:
                    issue("MISSING_SPAN", "Resolved cell has null spans.")
                    continue
                rs, cs = CellToRowAlignment().align(
                    cell.source_bbox, table.rows, table.columns, table.policy.tolerance
                )
                expected = {(r, c) for r in rs for c in cs}
                if (
                    expected != set(cell.occupied_slots)
                    or len(rs) != cell.rowspan
                    or len(cs) != cell.colspan
                    or rs[0] != cell.row_index
                    or cs[0] != cell.column_index
                ):
                    issue(
                        "SPAN_OCCUPANCY_CONFLICT", "Span disagrees with geometric grid occupancy."
                    )
                for r, c in expected:
                    for nr, nc, key in [
                        (r - 1, c, ("horizontal", r, c)),
                        (r + 1, c, ("horizontal", r + 1, c)),
                        (r, c - 1, ("vertical", r, c)),
                        (r, c + 1, ("vertical", r, c + 1)),
                    ]:
                        edge = edges.get(key)
                        expected_state = "absent" if (nr, nc) in expected else "present"
                        allowed = (
                            ("present", "inferred")
                            if expected_state == "present" and cell.status == "inferred"
                            else (expected_state,)
                        )
                        if edge is None or edge.status not in allowed:
                            issue(
                                "CELL_BOUNDARY_CONFLICT",
                                "Resolved enclosure conflicts with observed boundaries.",
                            )
                for source_id in cell.source_cell_ids:
                    source = sources[source_id]
                    if close_box(source.source.bbox, cell.source_bbox, table.policy.tolerance):
                        if source.text_raw is not None and cell.original_text != source.text_raw:
                            issue("SOURCE_TEXT_CHANGED", "Exact source cell text changed.")
            elif cell.rowspan is not None or cell.colspan is not None:
                issue("UNCONFIRMED_SPAN", "Unresolved cell must have null spans.")
        expected = {(r.index, c.index) for r in table.rows for c in table.columns}
        if set(occupancy) != expected:
            issue("CELL_COVERAGE_GAP", "Reconstruction does not account for every elementary slot.")
        linked = set()
        for link in table.source_links:
            if link.source_cell_id not in sources or (
                link.reconstructed_cell_id is not None and link.reconstructed_cell_id not in ids
            ):
                issue("SOURCE_LINK_INVALID", "Source link points to a nonexistent cell.")
            if link.reconstructed_cell_id is not None:
                linked.add(link.source_cell_id)
        if set(sources) - linked != set(table.unassigned_source_cell_ids):
            issue("SOURCE_COVERAGE_CONFLICT", "Unassigned source-cell inventory is inconsistent.")
        if table.unassigned_source_cell_ids:
            issue(
                "SOURCE_CELL_UNASSIGNED",
                "Original cells retained without a resolved overlap link.",
                "warning",
            )
        if any(c.status == "inferred" for c in table.cells) and not any(
            i.code == "INFERRED_BOUNDARY_EVIDENCE" for i in issues
        ):
            issue("INFERRED_BOUNDARY_EVIDENCE", "Inferred cells require review.", "warning")
        unresolved = any(c.status != "resolved" for c in table.cells) or bool(
            table.unresolved_boundaries
        )
        state = (
            "invalid"
            if any(i.severity == "error" for i in issues)
            else "review_required"
            if not table.cells
            else "partial"
            if unresolved or any(i.severity == "warning" for i in issues)
            else "validated"
        )
        return table.model_copy(
            update={"analysis_state": state, "validation_issues": tuple(issues)}
        )
