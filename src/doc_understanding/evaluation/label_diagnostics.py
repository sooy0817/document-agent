"""Read-only blank-label diagnosis. Geometry suggests a split, never a repair.

An extracted blank is an observation by the source extractor, not proof that the
author intended an empty heading. Keep missing provenance distinct from blanks.
"""
from collections import Counter


def area(box):
    return max(0, box.x1-box.x0)*max(0, box.y1-box.y0)


def overlap(a, b):
    return max(0, min(a.x1,b.x1)-max(a.x0,b.x0))*max(0, min(a.y1,b.y1)-max(a.y0,b.y0))


def blank_node(node, table, page):
    cells = {c.cell_id: c for c in table.cells}
    cell = cells.get(node.source_cell_id)
    result = dict(node_id=node.node_id, axis=node.axis, source_cell_id=node.source_cell_id,
                  linked_source_cell_ids=[], span_ids=[], sibling_cell_ids=[])
    if cell is None:
        return dict(result, cause='PHYSICAL_REFERENCE_MISSING')
    if (cell.original_text or '').strip():
        return dict(result, cause='STRUCTURE_LABEL_MISSING')
    linked = set(cell.source_cell_ids)
    linked.update(l.source_cell_id for l in table.source_links if l.reconstructed_cell_id==cell.cell_id)
    sources = [c for c in table.source_cells if c.cell_id in linked
               and c.source.document_id==table.document_id and c.source.page_number==table.page_number]
    result['linked_source_cell_ids'] = sorted(c.cell_id for c in sources)
    spans = [s for b in page.blocks for s in b.spans] if page else []
    inside = [s.span_id for s in spans if s.text_raw.strip()
              and s.source.document_id==table.document_id and s.source.page_number==table.page_number
              and area(s.source.bbox)>0 and overlap(s.source.bbox,cell.source_bbox)>=.85*area(s.source.bbox)]
    result['span_ids'] = inside
    if inside:
        return dict(result, cause='SOURCE_SPAN_PRESENT')
    for source in sources:
        if not (source.text_raw or '').strip():
            continue
        box = source.source.bbox
        if area(box)<=1.05*area(cell.source_bbox) or overlap(box,cell.source_bbox)<.95*area(cell.source_bbox):
            continue
        sibling_ids = {l.reconstructed_cell_id for l in table.source_links if l.source_cell_id==source.cell_id}
        siblings = [c for c in table.cells if c.cell_id!=cell.cell_id and (c.original_text or '').strip()
                    and (source.cell_id in c.source_cell_ids or c.cell_id in sibling_ids)
                    and overlap(c.source_bbox,box)>=.95*area(c.source_bbox)]
        if siblings:
            result['sibling_cell_ids'] = sorted(c.cell_id for c in siblings)
            return dict(result, cause='MERGED_SOURCE_SPLIT_CANDIDATE')
    if any((c.text_raw or '').strip() for c in sources):
        return dict(result, cause='SOURCE_TEXT_PRESENT')
    covering = [c for c in sources if area(cell.source_bbox)>0
                and overlap(c.source.bbox,cell.source_bbox)>=.95*area(cell.source_bbox)]
    if covering and all(c.extraction_status=='blank' for c in covering) and page is not None:
        return dict(result, cause='SOURCE_EXTRACTED_BLANK')
    if any(c.extraction_status=='unreadable' for c in sources) or cell.original_text is None:
        return dict(result, cause='SOURCE_UNREADABLE')
    return dict(result, cause='SOURCE_PROVENANCE_INSUFFICIENT')


def label_diagnostics(table, structure, page, view):
    """Return one mutually exclusive cause per affected value, plus original nodes.

    Multiple distinct node causes are MIXED; separate all-blocker and first-
    blocker counters so counts remain comparable with the usability baseline.
    """
    nodes = {n.node_id:n for n in structure.row_tree+structure.column_tree}
    mappings = {m.cell_id:m for m in structure.cell_mappings}
    diagnoses = {}
    counts, first, axes = Counter(), Counter(), Counter()
    examples = {}
    for cell in view['cells']:
        if 'LABEL_UNAVAILABLE' not in cell['blockers'] or cell['mapping_state'] is None:
            continue
        mapping = mappings[cell['id']]
        missing = [nodes[n] for n in mapping.row_path+mapping.column_path if not (nodes[n].label_raw or '').strip()]
        for node in missing:
            if node.node_id not in diagnoses:
                diagnoses[node.node_id] = blank_node(node,table,page)
        causes = sorted({diagnoses[n.node_id]['cause'] for n in missing})
        cause = causes[0] if len(causes)==1 else 'MIXED'
        counts[cause] += 1
        if cell['blockers'][0]=='LABEL_UNAVAILABLE': first[cause] += 1
        axes['+'.join(sorted({n.axis for n in missing}))] += 1
        examples.setdefault(cause,dict(cell_id=cell['id'],r=cell['r'],c=cell['c'],
                                       blank_nodes=[diagnoses[n.node_id] for n in missing]))
    return dict(first_reason=dict(first), all_blockers=dict(counts), axes=dict(axes),
                nodes_by_cause=dict(Counter(d['cause'] for d in diagnoses.values())),
                examples=examples)
