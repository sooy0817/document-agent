"""Rebuild affected structure from archived physical evidence, without extraction or IO."""
from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer


def rebuild(doc):
    from doc_understanding.parser.assembly.continued_sections import coalesce_reprints
    from doc_understanding.parser.assembly.document import numbered_view, validate_document
    from doc_understanding.parser.relations.header_inheritance import inherit
    from doc_understanding.parser.relations.logical_tables import build_logical_links
    pages={p.page_number:p for p in doc.document_ir.pages}
    physical={t.table_id:t for t in doc.physical_tables}
    tables=[]
    for st in doc.table_structure:
        # Preserve recorded layout/scope review and unrelated row/path decisions.
        # Only the header hypothesis being changed is regenerated from original cells.
        affected=any(a.code=='HEADER_REGION_AMBIGUOUS' for a in st.ambiguities)
        affected |= any(i.code=='BORROWED_HEADER_REQUIRED' for i in st.issues)
        reviewed=any(i.code=='STRUCTURE_REVIEW_LAYOUT' for i in st.issues)
        if affected and not reviewed:
            fresh=SemanticTableAnalyzer().analyze(physical[st.table_id],source_page=pages[st.page_number])
            fresh=numbered_view(fresh,physical[st.table_id])
            st=fresh.model_copy(update={'analysis_run_id':st.analysis_run_id})
        tables.append(st)
    tables,links=inherit(doc.document_ir,doc.physical_tables,tables,
                        build_logical_links(doc.document_ir,doc.physical_tables))
    result=doc.model_copy(update=dict(table_structure=tables,logical_table_links=links,
        column_hierarchy=tuple(n for t in tables for n in t.column_tree),
        row_hierarchy=tuple(n for t in tables for n in t.row_tree),
        cell_mappings=tuple(m for t in tables for m in t.cell_mappings)))
    result = coalesce_reprints(result)
    validate_document(result)
    assert result.document_ir is doc.document_ir and result.physical_tables is doc.physical_tables
    return result
