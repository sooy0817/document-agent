"""Source-bound rule candidates shared by review routing and comparison reports."""

from typing import Literal

from pydantic import ValidationError

from doc_understanding.domain.models import EvidenceModel
from doc_understanding.domain.pipeline_contracts import CanonicalDocument
from doc_understanding.parser.relations.logical_tables import build_logical_links, validate_logical_links
from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer, physical_state


class SemanticCandidate(EvidenceModel):
    cell_id: str
    row_ids: tuple[str, ...] = ()
    column_ids: tuple[str, ...] = ()
    role: Literal['value', 'header', 'description', 'blank', 'unknown'] = 'unknown'
    role_uncertain: bool = True
    basis: Literal['geometry', 'page_continuation', 'unavailable'] = 'unavailable'
    reasons: tuple[str, ...] = ()
    context_ids: tuple[str, ...] = ()
    semantic_verified: Literal[False] = False


def continuation_links(doc, pages):
    """Retain complete continuation ancestry; never enable deferred detached headers."""
    links = build_logical_links(doc.document_ir, doc.physical_tables)
    validate_logical_links(doc.model_copy(update={'logical_table_links': links}))
    by_target = {link.target_table_id: link for link in links}
    tables = {t.table_id: t for t in doc.physical_tables}
    retained = set()
    for link in links:
        if tables[link.target_table_id].page_number not in pages:
            continue
        chain, seen = [], set()
        while link:
            if link.kind != 'page_continuation' or link.link_id in seen:
                chain = []
                break
            seen.add(link.link_id)
            chain.append(link.link_id)
            link = by_target.get(link.source_table_id)
        retained.update(chain)
    return tuple(link for link in links if link.link_id in retained)


def generate(raw, target_ids):
    """Legacy/minimal snapshots go to review; a schema failure never grants a skip."""
    if len(target_ids) != len(set(target_ids)):
        raise ValueError('DUPLICATE_REVIEW_TARGET')
    try:
        doc = CanonicalDocument.model_validate(raw)
    except ValidationError:
        return [SemanticCandidate(cell_id=cid, reasons=('RULE_INPUT_UNAVAILABLE',)) for cid in target_ids]
    if doc.document_ir is None:
        return [SemanticCandidate(cell_id=cid, reasons=('RULE_INPUT_UNAVAILABLE',)) for cid in target_ids]
    owners = {c.cell_id: t for t in doc.physical_tables for c in t.cells}
    cells = {c.cell_id: c for t in doc.physical_tables for c in t.cells}
    if any(cid not in cells for cid in target_ids):
        raise ValueError('SELECTED_CELL_NOT_FOUND')
    pages = {owners[cid].page_number for cid in target_ids}
    links = continuation_links(doc, pages)
    logical = {p.cell_id: (p, link) for link in links for p in link.paths}
    # A separate same-page header is explicitly deferred for this workstream.
    all_links = build_logical_links(doc.document_ir, doc.physical_tables)
    deferred = {tid for link in all_links if link.kind == 'detached_header'
                for tid in (link.source_table_id, link.target_table_id)}
    source_pages = {p.page_number: p for p in doc.document_ir.pages}
    structures = {}
    result = []
    for cid in target_ids:
        table, cell = owners[cid], cells[cid]
        reasons, row_ids, column_ids = [], (), ()
        role, uncertain, basis = 'unknown', True, 'geometry'
        if table.table_id in deferred:
            reasons.append('DEFERRED_FRAGMENT')
        if cid in logical:
            path, link = logical[cid]
            row_ids, column_ids = path.row_source_cell_ids, path.column_source_cell_ids
            basis, uncertain = 'page_continuation', False
            reasons.extend(r for r in path.reasons if r != 'SOURCE_GEOMETRY_CANDIDATE')
            if path.state == 'unresolved':
                reasons.append('PATH_UNRESOLVED')
        else:
            if table.table_id not in structures:
                structures[table.table_id] = SemanticTableAnalyzer().analyze(
                    table, source_page=source_pages[table.page_number])
            st = structures[table.table_id]
            nodes = {n.node_id: n for n in st.row_tree + st.column_tree}
            mapping = next((m for m in st.cell_mappings if m.cell_id == cid), None)
            cell_role = next((r.role for r in st.cell_roles if r.cell_id == cid), 'unknown')
            role = {'row_label': 'header', 'column_header': 'header', 'corner': 'header'}.get(cell_role, cell_role)
            relevant = [a.code for a in st.ambiguities if cid in a.target_ids or not a.target_ids]
            layout_uncertain = any(a.code in ('HEADER_REGION_AMBIGUOUS', 'PANEL_SPLIT_AMBIGUOUS')
                                   for a in st.ambiguities)
            suspect_header = any(p.header_rows >= len(table.rows) - 1 and p.header_rows > 2 for p in st.panels)
            uncertain = cell_role == 'unknown' or layout_uncertain or suspect_header
            if uncertain:
                reasons.append('ROLE_AMBIGUOUS')
            if suspect_header:
                reasons.append('HEADER_BODY_CONFLICT')
            if st.analysis_state == 'invalid':
                reasons.append('STRUCTURE_INVALID')
            if any(k in relevant for k in ('PARENT_AMBIGUOUS', 'PATH_COLLISION')):
                reasons.append('COMPETING_PATHS')
            if mapping:
                row_ids = tuple(nodes[n].source_cell_id for n in mapping.row_path)
                column_ids = tuple(nodes[n].source_cell_id for n in mapping.column_path)
                if mapping.evidence_state == 'unresolved':
                    reasons.append('PATH_UNRESOLVED')
            else:
                reasons.append('PATH_MISSING')
        refs = row_ids + column_ids + (cid,)
        if any(physical_state(cells[s], owners[s]) == 'unresolved' for s in refs):
            reasons.append('PHYSICAL_CONTEXT_UNRESOLVED')
        if cell.row_index is not None and cell.column_index is not None:
            if any(c.row_index is not None and c.column_index is not None and c.rowspan and c.colspan
                   and c.column_index + c.colspan <= cell.column_index
                   and c.row_index < cell.row_index + (cell.rowspan or 1)
                   and c.row_index + c.rowspan > cell.row_index
                   and physical_state(c, table) == 'unresolved' for c in table.cells):
                reasons.append('PHYSICAL_CONTEXT_UNRESOLVED')
        result.append(SemanticCandidate(
            cell_id=cid, row_ids=row_ids, column_ids=column_ids, role=role,
            role_uncertain=uncertain, basis=basis, reasons=tuple(sorted(set(reasons))),
            context_ids=tuple(dict.fromkeys(row_ids + column_ids))))
    return result
