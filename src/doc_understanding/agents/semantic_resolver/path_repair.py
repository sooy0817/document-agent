"""Opt-in geometry repair; source IR immutable, uncertain relations quarantined."""
from copy import deepcopy


def repair_paths(physical, structured, parents):
    result = deepcopy(structured)
    edges = dict(parents)
    trace = []
    if len(result['panels']) != 1 or result['panels'][0]['stub_columns'] != 1:
        return result, edges, [{'action': 'ABSTAIN', 'reason': 'unsupported_panel_layout'}]
    panel = result['panels'][0]
    cells = {c['cell_id']: c for c in physical['cells']}
    rows = {}
    for c in cells.values():
        rows.setdefault(c['row_index'], []).append(c)
    nodes = {n['source_cell_id']: n for n in result['row_tree']}
    # A unique leftmost text cell with same-row values can label a merged stub.
    for row, cs in rows.items():
        if row < panel['header_rows']:
            continue
        left = [c for c in cs if c['column_index']==0 and c['rowspan']==1 and (c['original_text'] or '').strip()]
        if len(left)!=1:
            continue
        c = left[0]
        if c['cell_id'] in nodes or c['colspan'] <= 1:
            continue
        values = [v for v in cs if v['column_index']>=c['colspan']]
        if not values:
            continue
        node = {'node_id': f"{panel['panel_id']}/row/{c['cell_id']}", 'axis':'row', 'panel_id':panel['panel_id'], 'label_raw':c['original_text'], 'parent_id':None, 'candidate_parent_ids':[], 'source_cell_id':c['cell_id'], 'bbox':c['source_bbox'], 'row_range':[row,row+1], 'column_range':[0,c['colspan']], 'physical_evidence':'unresolved' if c['status']=='unresolved' else 'inferred', 'evidence_state':'unresolved', 'boundary_ids':c['boundary_ids']}
        result['row_tree'].append(node)
        nodes[c['cell_id']] = node
        for m in result['cell_mappings']:
            if m['cell_id'] in {v['cell_id'] for v in values} and not m['row_path']:
                m['row_path'] = [node['node_id']]
                m['row_candidates'] = [node['node_id']]
                m['evidence_state'] = 'unresolved'
                trace.append({'action':'MAP_ROW', 'cell_id':m['cell_id'], 'label_cell_id':c['cell_id'], 'status':'review_required'})
    # Do not declare these roots: suppress dubious semantic edges only in candidate view.
    for child, parent in list(edges.items()):
        if child not in cells:
            continue
        c, p = cells[child], cells.get(parent)
        reason = None
        if p is None:
            reason = 'parent_outside_table_requires_cross_page_evidence'
        elif c['column_index']==p['column_index']==0 and c['row_index']>p['row_index'] and p['rowspan']==1:
            populated = any(v['column_index']>=p['colspan'] and (v['original_text'] or '').strip() for v in rows[p['row_index']])
            if populated:
                reason = 'same_column_populated_peer_requires_semantic_evidence'
        if reason:
            del edges[child]
            trace.append({'action':'QUARANTINE', 'child_cell_id':child, 'previous_parent_cell_id':parent, 'reason':reason, 'status':'unresolved', 'root_confirmed':False})
    return result, edges, trace
