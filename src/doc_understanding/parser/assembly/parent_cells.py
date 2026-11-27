"""Resolve direct containment to canonical physical cell IDs for export and retrieval."""
from collections import Counter, defaultdict

from doc_understanding.parser.assembly.nested_content import build_nested_content, contains


def parent_cell_refs(doc, nested=None):
    """Yield (child table ID, parent cell ID or None, unresolved reason or None)."""
    ir = doc.document_ir
    if ir is None or all(n == 1 for n in Counter(t.page_number for t in doc.physical_tables).values()):
        return
    physical_cells = {cell.cell_id: (table, cell)
                      for table in doc.physical_tables for cell in table.cells}

    if ir.source.media_type == "text/html":
        for child in doc.physical_tables:
            refs = [ref.cell_id for ref in child.source_references if ref.cell_id]
            if not refs:
                continue
            if len(refs) == 1 and refs[0] in physical_cells:
                parent, cell = physical_cells[refs[0]]
                if parent.page_number == child.page_number and parent.table_id != child.table_id:
                    yield child.table_id, cell.cell_id, None
                    continue
            yield child.table_id, None, "HTML_PARENT_CELL_REFERENCE_INVALID"
        return
    if ir.source.media_type != "application/pdf":
        return

    nested = nested if nested is not None else build_nested_content(doc)
    linked = {(cell.parent_cell_id, child) for cell in nested.cells
              if cell.containment_status == "linked" for child in cell.child_table_ids}
    # Native parent cell IDs and reconstructed cell IDs belong to different grids.
    exact = defaultdict(set)
    overlaps = defaultdict(set)
    child_boxes = {t.table_id: t.bounding_box for t in doc.physical_tables}
    for table in doc.physical_tables:
        for link in table.source_links:
            if link.relation == "exact" and link.reconstructed_cell_id in physical_cells:
                exact[link.source_cell_id].add(link.reconstructed_cell_id)
            elif link.relation == "overlap" and link.reconstructed_cell_id in physical_cells:
                overlaps[link.source_cell_id].add(link.reconstructed_cell_id)

    by_child = defaultdict(list)
    for candidate in nested.candidates:
        for child in candidate.physical_table_ids:
            by_child[child].append(candidate)
    for child, candidates in by_child.items():
        if len(candidates) != 1:
            yield child, None, "COMPETING_CONTAINMENT_CANDIDATES"
            continue
        candidate = candidates[0]
        owner = candidate.selected_parent_id
        if (owner, child) not in linked or len(candidate.physical_table_ids) != 1:
            yield child, None, (", ".join(r for r in candidate.reasons if r != "STRICT_BBOX_CONTAINMENT")
                       or "PARENT_CONTAINMENT_UNRESOLVED")
            continue
        matches = exact.get(owner, set())
        if not matches:
            # A source cell can overlap several reconstructed cells at a border.
            # Only an identified parent cell enclosing the entire child is eligible.
            matches = {cid for cid in overlaps.get(owner, ())
                       if physical_cells[cid][0].page_number == candidate.page_number
                       and physical_cells[cid][0].table_id != child
                       and contains(child_boxes[child], physical_cells[cid][1].source_bbox)}
        if len(matches) == 1:
            parent, cell = physical_cells[next(iter(matches))]
            if parent.table_id != child and parent.page_number == candidate.page_number:
                yield child, cell.cell_id, None
                continue
        yield child, None, "NATIVE_PARENT_HAS_NO_UNIQUE_GRID_CELL"
