"""Describe withheld paths without changing trees, mappings or eligibility."""
from collections import Counter, defaultdict

from doc_understanding.parser.structure.trees import contains, leaves_for, ranges
from doc_understanding.rag.evidence.cells import label_key

REASONS = ('ROW_PATH_MISSING', 'COLUMN_PATH_MISSING', 'PATH_COLLISION')


def missing_path(axis, cell, mapping, structure, table_codes):
    if 'PANEL_SPLIT_AMBIGUOUS' in table_codes:
        return 'PANEL_SPLIT_AMBIGUOUS'
    panel = next((p for p in structure.panels if p.panel_id==mapping.panel_id),None)
    nodes = [n for n in (structure.row_tree if axis=='row' else structure.column_tree)
             if n.panel_id==mapping.panel_id]
    if not nodes:
        absent = panel is not None and (panel.stub_columns if axis=='row' else panel.header_rows)==0
        return 'AXIS_NOT_DECLARED' if absent else 'NO_LABEL_NODES'
    if not cell.occupied_slots:
        return 'PHYSICAL_OCCUPANCY_MISSING'
    candidates = leaves_for(cell,nodes,axis)
    if not candidates:
        return 'NO_COVERING_LABEL'
    if len(candidates)>1:
        if axis=='column' and any(not contains(n.column_range,ranges(cell)[1]) for n in candidates):
            return 'VALUE_SPANS_MULTIPLE_HEADERS'
        return 'MULTIPLE_LABEL_CANDIDATES'
    return 'PATH_WITHHELD_WITH_SINGLE_CANDIDATE'


def path_diagnostics(table, structure, view):
    cells = {c.cell_id:c for c in table.cells}
    mappings = {m.cell_id:m for m in structure.cell_mappings}
    id_groups, text_groups = defaultdict(list), defaultdict(list)
    for m in structure.cell_mappings:
        if m.row_path and m.column_path:
            id_groups[m.row_path,m.column_path].append(m.cell_id)
    for c in view['cells']:
        if c['mapping_state'] is not None:
            text_groups[label_key(c['row_path']),label_key(c['column_path'])].append(c['id'])
    counts = {r:Counter() for r in REASONS}
    first = {r:Counter() for r in REASONS}
    examples = {}
    for c in view['cells']:
        if c['mapping_state'] is None:
            continue
        m = mappings[c['id']]
        for reason in REASONS:
            if reason not in c['blockers']:
                continue
            if reason!='PATH_COLLISION':
                kind = missing_path('row' if reason=='ROW_PATH_MISSING' else 'column',
                                    cells[c['id']],m,structure,view['table_codes'])
            else:
                peers = id_groups[m.row_path,m.column_path]
                if len(peers)>1:
                    same_rows = len({(cells[i].row_index,cells[i].rowspan) for i in peers})==1
                    kind = 'SAME_NODE_PATH_SAME_ROW' if same_rows else 'SAME_NODE_PATH_DIFFERENT_ROWS'
                else:
                    peers = text_groups[label_key(c['row_path']),label_key(c['column_path'])]
                    different_rows = len({(cells[i].row_index,cells[i].rowspan) for i in peers})>1
                    kind = 'SAME_TEXT_PATH_DIFFERENT_ROWS' if different_rows else 'UNCLASSIFIED_COLLISION'
            counts[reason][kind] += 1
            if c['blockers'][0]==reason:first[reason][kind] += 1
            examples.setdefault(reason+'/'+kind,dict(cell_id=c['id'],r=c['r'],c=c['c'],
                row_path=list(m.row_path),column_path=list(m.column_path),
                row_candidates=list(m.row_candidates),column_candidates=list(m.column_candidates)))
    return dict(first_reason={k:dict(v) for k,v in first.items()},
                all_blockers={k:dict(v) for k,v in counts.items()},examples=examples)
