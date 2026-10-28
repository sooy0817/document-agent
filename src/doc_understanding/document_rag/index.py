"""Disposable search projections; values are always rehydrated from Document JSON."""
import re
from decimal import Decimal, InvalidOperation


def parse_value(raw):
    if raw is None:
        return 'unavailable', None
    value = re.sub(r'\s+', '', raw)
    if value == '':
        return 'blank', None
    if value in ('-', '－', '—'):
        return 'hyphen', None
    if value in ('N/A', '해당없음'):
        return 'not_applicable', None
    triangle = value.startswith(('△', '▲'))
    candidate = '-' + value[1:] if triangle else value
    if re.fullmatch(r'\(\d[\d,.]*\)', candidate):
        candidate = '-' + candidate[1:-1]
    if not re.fullmatch(r'[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?', candidate):
        return 'text', None
    try:
        parsed = str(Decimal(candidate.rstrip('%').replace(',', '')))
    except InvalidOperation:
        return 'text', None
    # A triangle's accounting meaning requires downstream validation.
    return ('triangle_decrease_candidate' if triangle else 'number'), parsed


def display_path(path):
    return ' > '.join(x if x is not None else '[unavailable label]' for x in path)


def cells_for_table(doc, table_id):
    st = next(t for t in doc.table_structure if t.table_id == table_id)
    pt = next(t for t in doc.physical_tables if t.table_id == table_id)
    nodes = {n.node_id: n for n in st.row_tree + st.column_tree}
    cells = {c.cell_id: c for c in pt.cells}
    tc = next(c for c in doc.contents if c.table_id == table_id)
    contents = {c.content_id: c for c in doc.contents}
    context = [contents[cid] for cid in tc.context_content_ids]
    units = [dict(content_id=c.content_id, text_raw=c.text_raw, status='candidate')
             for c in context if re.search(r'단\s*위\s*[:：]|\bunit\s*:', c.text_raw or '', re.I)]
    result = []
    for m in st.cell_mappings:
        cell = cells[m.cell_id]
        kind, number = parse_value(cell.original_text)
        notes = []
        for link in doc.context_links:
            if link.relation != 'note':
                continue
            applies = link.target_id == table_id or link.target_id in m.row_path or link.target_id == cell.cell_id
            if applies or (link.scope == 'unknown' and link.source.page_number == pt.page_number):
                note = next((c for c in context if link.source in c.source_references), None)
                if note:
                    notes.append(dict(text_raw=note.text_raw, target_id=link.target_id, scope=link.scope,
                                      status='candidate' if applies else 'unknown_scope'))
        row_labels = [nodes[n].label_raw for n in m.row_path]
        column_labels = [nodes[n].label_raw for n in m.column_path]
        eligible = bool(row_labels and column_labels and all(row_labels + column_labels)
                        and m.evidence_state != 'unresolved' and cell.status != 'unresolved')
        header_rows = max((p.header_rows for p in st.panels), default=1)
        table_context = [c.original_text for c in pt.cells if c.row_index is not None
                         and c.row_index < header_rows and c.original_text]
        result.append(dict(document_id=doc.document_id, source_name=doc.document_ir.source_name,
                           source_uri=doc.source_uri,source_media_type=doc.document_ir.source.media_type,
                           page=pt.page_number, table_id=table_id, cell_id=cell.cell_id,
                           value_raw=cell.original_text, value_kind=kind, numeric_candidate=number,
                           row_node_ids=list(m.row_path), column_node_ids=list(m.column_path),
                           row_path=row_labels, column_path=column_labels,
                           lookup_eligible=eligible, table_context=table_context,
                           mapping_state=m.evidence_state, physical_state=cell.status,
                           semantic_status='unvalidated_candidate', unit_context=units, notes=notes,
                           source=cell.source.model_dump(mode='json'),
                           source_cell_ids=list(cell.source_cell_ids),
                           span_refs=list(cell.span_refs),
                           context_content_ids=list(tc.context_content_ids)))
    return result


def make_entries(doc):
    contents = {c.content_id: c for c in doc.contents}
    sections = {s.section_id: s.title_raw for s in doc.sections}
    entries = []
    for c in doc.contents:
        if c.duplicate_of_table_ids:
            continue
        context = [contents[cid].text_raw or '' for cid in c.context_content_ids]
        prefix = [doc.document_ir.source_name, sections.get(c.section_id, '')] + context
        if c.kind == 'table':
            table = next(t for t in doc.table_structure if t.table_id == c.table_id)
            rows = cells_for_table(doc, c.table_id)
            pt=next(t for t in doc.physical_tables if t.table_id==c.table_id)
            header_count=max((p.header_rows for p in table.panels),default=1)
            header_text=[cell.original_text or '' for cell in pt.cells
                         if cell.row_index is not None and cell.row_index<header_count]
            primary = header_text + [n.label_raw or '' for n in table.row_tree + table.column_tree]
            summary = prefix + primary
            entries.append(dict(entry_id=c.content_id, content_id=c.content_id, kind='table',
                                table_id=c.table_id, page=table.page_number, cell_ids=[],
                                text='\n'.join(summary),primary_text='\n'.join(primary),
                                context_content_ids=list(c.context_content_ids)))
            by_row = {}
            for row in rows:
                key = tuple(row['row_node_ids'])
                by_row.setdefault(key, []).append(row)
            for i, values in enumerate(by_row.values()):
                primary = header_text + [display_path(values[0]['row_path'])]
                primary += [display_path(v['column_path']) + ': ' + (v['value_raw'] if v['value_raw'] is not None else '[unavailable]')
                         for v in values]
                text = prefix + primary
                entries.append(dict(entry_id=c.content_id + f'/row{i}', content_id=c.content_id,
                                    kind='table_row', table_id=c.table_id, page=table.page_number,
                                    cell_ids=[v['cell_id'] for v in values], text='\n'.join(text),primary_text='\n'.join(primary),
                                    context_content_ids=list(c.context_content_ids)))
        elif (c.text_raw or '').strip():
            entries.append(dict(entry_id=c.content_id, content_id=c.content_id, kind=c.kind,
                                table_id=None, page=c.source_references[0].page_number, cell_ids=[],
                                text='\n'.join(prefix + [c.text_raw]),primary_text=c.text_raw, context_content_ids=[]))
    return entries


def terms(text):
    """Transparent Korean bigram/substring fallback, not a morphological analyzer."""
    tokens = re.findall(r'[가-힣]+|[A-Za-z]+|\d+', text.lower())
    result = set(tokens)-{'the','a','an','of','in','is','what','for','to','and','from','are','be','by','as'}
    for token in tokens:
        if re.fullmatch(r'[가-힣]{3,}', token):
            result.update(token[i:i+2] for i in range(len(token)-1))
    return sorted(result)[:80]


def lexical_score(text, query):
    text = text.lower()
    qterms = terms(query)
    return sum(min(len(t), 6) for t in qterms if t in text) / max(1, len(qterms))


def evidence_pack(doc, hits, question, max_cells=35):
    """Bound context to retrieved rows; query scores order evidence, never create values."""
    selected, seen = [], set()
    for hit in hits:
        if hit.get('table_id'):
            for v in cells_for_table(doc, hit['table_id']):
                if not v['lookup_eligible']:
                    continue
                if hit['kind'] == 'table_row' and v['cell_id'] not in hit['cell_ids']:
                    continue
                if v['cell_id'] not in seen:
                    selected.append(v)
                    seen.add(v['cell_id'])
    def score(v):
        return lexical_score(display_path(v['row_path'] + v['column_path']), question)
    selected.sort(key=score, reverse=True)
    selected = selected[:max_cells]
    for i, v in enumerate(selected, 1):
        v['evidence_id'] = f'E{i}'
    # Include adjacent body and notes even for a table-only hit.
    context_ids = {cid for h in hits for cid in h.get('context_content_ids', [])}
    context_ids |= {h['content_id'] for h in hits if not h.get('table_id')}
    for c in doc.contents:
        if c.content_id in context_ids and c.text_raw and not c.duplicate_of_table_ids:
            selected.append(dict(evidence_id=f'E{len(selected)+1}', document_id=doc.document_id,
                                 source_name=doc.document_ir.source_name,
                                 source_uri=doc.source_uri, source_media_type=doc.document_ir.source.media_type,
                                 page=c.source_references[0].page_number, table_id=None, cell_id=None,
                                 content_id=c.content_id, value_raw=c.text_raw, value_kind='body',
                                 row_path=[], column_path=[], unit_context=[], notes=[],
                                 semantic_status='unvalidated_candidate',
                                 source=c.source_references[0].model_dump(mode='json')))
    return selected
