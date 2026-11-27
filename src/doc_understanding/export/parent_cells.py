"""Expose direct table containment using the cell numbers of the public grid."""
from doc_understanding.parser.assembly.parent_cells import parent_cell_refs


def attach_parent_cells(doc, cleaned):
    public = {t.table_id: out for t, out in zip(doc.physical_tables, cleaned, strict=True)}
    refs = {c.cell_id: (public[t.table_id]["table_id"], n)
            for t in doc.physical_tables for n, c in enumerate(t.cells, 1)}
    for child, owner, reason in parent_cell_refs(doc):
        if reason:
            public[child].setdefault("open_questions", []).append(dict(code="PARENT_CELL_UNRESOLVED", reason=reason))
        else:
            table_id, cell_id = refs[owner]
            state = "observed" if doc.document_ir.source.media_type == "text/html" else "inferred"
            public[child]["parent_cell"] = dict(table_id=table_id, cell_id=cell_id, state=state)
