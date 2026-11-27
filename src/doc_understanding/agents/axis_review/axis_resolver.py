# 역할: 행열 관계의 근거 영역과 규칙 후보를 구성한다. 추출 결과는 불변 입력으로 취급한다.
"""Opt-in, source-anchored axis resolution. Never mutates an extraction IR.

Logical regions group existing fragments using a unique native table container.
Proposals are geometry checked, not certified semantic facts. No domain vocabulary.
"""
import re
from collections import Counter
from typing import Literal

from doc_understanding.domain.axis_regions import (  # noqa: F401  (기존 import 경로 유지)
    AxisRegion, CellEvidence, area, digest, encloses, overlap,
)
from doc_understanding.domain.models import BBox, EvidenceModel


class AxisLink(EvidenceModel):
    cell_id: str
    row_path: list[str]
    column_path: list[str]
    row_axis: Literal['present', 'absent', 'unknown']
    column_axis: Literal['present', 'absent', 'unknown']
    reason: str


class AxisProposal(EvidenceModel):
    links: list[AxisLink]
    abstain_cell_ids: list[str]


class CheckedLink(EvidenceModel):
    proposal: AxisLink
    status: Literal['supported_candidate', 'unresolved']
    errors: tuple[str, ...]
    original_text: str | None
    semantic_verified: Literal[False] = False


def norm(text):
    return re.sub(r'\s+', '', text or '')


def band_match(a, b):
    return abs(a.x0-b.x0) <= 1.5 and abs(a.x1-b.x1) <= 1.5


def regions_for(page, tables):
    """Keep ambiguous containers separate; do not fuse geometry or generate cells."""
    spans = {s.span_id: s for b in page.blocks for s in b.spans}
    grouped = {}
    containers = {t.table_candidate_id: t for t in page.tables}
    for t in tables:
        if t.page_number != page.page_number:
            continue
        candidates = [n for n in page.tables if encloses(n.source.bbox, t.bounding_box)]
        candidates.sort(key=lambda n: area(n.source.bbox))
        parent = candidates[0] if candidates and (len(candidates) == 1 or
                    area(candidates[0].source.bbox) < area(candidates[1].source.bbox)) else None
        key = parent.table_candidate_id if parent else t.table_id
        evidence = grouped.setdefault(key, [])
        for c in t.cells:
            owned = [spans[s] for s in c.span_refs if s in spans and spans[s].text_raw.strip()]
            # Text remains unchanged. Counter coverage tolerates glyph order, not missing characters.
            source_ok = bool(owned) and not (Counter(norm(c.original_text)) -
                                            Counter(norm(''.join(s.text_raw for s in owned))))
            source_ok = source_ok and all(encloses(c.source_bbox, s.source.bbox) for s in owned)
            filled = any(b.kind == 'drawing' and b.drawing_stroked is False
                         and len(b.drawing_commands) == 1 and b.drawing_commands[0].operator == 're'
                         and area(b.source.bbox) <= 1.5*area(c.source_bbox)
                         and encloses(b.source.bbox, c.source_bbox)
                         for b in page.blocks)
            occupied = set(c.occupied_slots)
            rectangular = bool(occupied) and c.rowspan is not None and c.colspan is not None and occupied == {
                (r, col) for r in range(c.row_index, c.row_index+c.rowspan)
                for col in range(c.column_index, c.column_index+c.colspan)}
            evidence.append(CellEvidence(id=c.cell_id, table_id=t.table_id,
                text=c.original_text or '', bbox=c.source_bbox, spans=c.span_refs,
                physical_usable=bool(rectangular and source_ok and c.status != 'unresolved'),
                row=c.row_index, col=c.column_index, rs=c.rowspan, cs=c.colspan,
                filled=filled, bold=bool(owned) and all(s.flags & 16 for s in owned)))
    result = []
    for key, cells in grouped.items():
        boxes = [c.bbox for c in cells]
        box = BBox(x0=min(b.x0 for b in boxes), y0=min(b.y0 for b in boxes),
                   x1=max(b.x1 for b in boxes), y1=max(b.y1 for b in boxes))
        result.append(AxisRegion(id=key+'/axis-region', document_id=tables[0].document_id,
                                 page=page.page_number, bbox=box,
                                 source_table_id=key if key in containers else None, cells=tuple(cells)))
    return result


def _numeric(text):
    return bool(re.fullmatch(r'[+\-△▲]?\d[\d,.]*(?:%|‰)?', norm(text)))


def _simple_rows(cells):
    rows = {}
    for c in cells:
        if c.physical_usable and norm(c.text) and c.rs == c.cs == 1:
            rows.setdefault(c.row, []).append(c)
    return [sorted(row, key=lambda c: c.bbox.x0) for _, row in sorted(rows.items())]


def rule_proposal(region):
    by_table = {}
    for c in region.cells:
        by_table.setdefault(c.table_id, []).append(c)
    links = {}
    # Complete flat matrices: distinct nonnumeric headers + independent text stubs
    # + >=2 body rows of numeric values. Still a hypothesis, not fact approval.
    for cells in by_table.values():
        if any(norm(c.text) and ((c.rs or 1)>1 or (c.cs or 1)>1) for c in cells):
            continue
        rows = _simple_rows(cells)
        if len(rows) < 3:
            continue
        heads, body = rows[0], rows[1:]
        if len(heads) < 2 or any(len(row) != len(heads) for row in body):
            continue
        if any(not norm(h.text) or _numeric(h.text) for h in heads):
            continue
        if len({norm(h.text) for h in heads}) != len(heads):
            continue
        if not all(not _numeric(row[0].text) and all(_numeric(c.text) for c in row[1:])
                   for row in body):
            continue
        if not all(all(band_match(h.bbox, c.bbox) for h, c in zip(heads, row)) for row in body):
            continue
        for row in body:
            for h, c in zip(heads[1:], row[1:]):
                links[c.id] = AxisLink(cell_id=c.id, row_path=[row[0].id], column_path=[h.id],
                    row_axis='present', column_axis='present',
                    reason='flat_matrix_text_headers_numeric_body_repeated_alignment')
    # Cross-fragment header inheritance: a common native container, aligned 2-cell
    # fragments, and an appearance band distinguishing header from right-hand body.
    pairs = [row for cells in by_table.values() for row in _simple_rows(cells) if len(row) == 2]
    pairs.sort(key=lambda row: row[0].bbox.y0)
    if region.source_table_id and len(pairs) >= 3:
        heads = pairs[0]
        body = [row for row in pairs[1:] if all(band_match(h.bbox, c.bbox) for h,c in zip(heads,row))]
        appearance = (all(c.filled for c in heads) and all(not row[1].filled for row in body)) or (
                      all(c.bold for c in heads) and all(not row[1].bold for row in body))
        if len(body) >= 2 and appearance and all(not _numeric(c.text) for c in heads):
            for row in body:
                links[row[1].id] = AxisLink(cell_id=row[1].id, row_path=[row[0].id],
                    column_path=[heads[1].id], row_axis='present', column_axis='present',
                    reason='shared_native_table_aligned_fragments_source_header_appearance')
    return AxisProposal(links=list(links.values()),
                        abstain_cell_ids=[c.id for c in region.cells if c.id not in links])


def check_proposal(region, proposal):
    cells = {c.id:c for c in region.cells}
    duplicates = Counter(p.cell_id for p in proposal.links)
    values = {p.cell_id for p in proposal.links}
    checked = []
    for p in proposal.links:
        errors = []
        ids = [p.cell_id]+p.row_path+p.column_path
        if any(i not in cells for i in ids):
            errors.append('UNKNOWN_CELL_ID')
        if duplicates[p.cell_id] > 1:
            errors.append('DUPLICATE_VALUE')
        if p.cell_id in proposal.abstain_cell_ids:
            errors.append('LINK_ABSTENTION_CONFLICT')
        if set(p.row_path+p.column_path) & values:
            errors.append('HEADER_VALUE_ROLE_CONFLICT')
        if len(set(p.row_path+p.column_path)) != len(p.row_path+p.column_path):
            errors.append('DUPLICATE_AXIS_NODE')
        for axis, path in [(p.row_axis,p.row_path),(p.column_axis,p.column_path)]:
            if axis == 'unknown' or (axis == 'present') != bool(path):
                errors.append('AXIS_STATUS_CONFLICT')
            # Absence is representable, but no automatic approval without separate proof.
            if axis == 'absent':
                errors.append('AXIS_ABSENCE_NEEDS_REVIEW')
        if not errors:
            v = cells[p.cell_id]
            if any(not cells[i].physical_usable for i in ids):
                errors.append('PHYSICAL_OR_SPAN_EVIDENCE_UNRESOLVED')
            for rid in p.row_path:
                r = cells[rid]
                if r.bbox.x1 > v.bbox.x0+1.5 or overlap(r.bbox,v.bbox,'y') < .5*min(
                        r.bbox.y1-r.bbox.y0,v.bbox.y1-v.bbox.y0):
                    errors.append('ROW_GEOMETRY_CONFLICT')
            for hid in p.column_path:
                h = cells[hid]
                if h.bbox.y1 > v.bbox.y0+1.5 or overlap(h.bbox,v.bbox,'x') < .85*(v.bbox.x1-v.bbox.x0):
                    errors.append('COLUMN_GEOMETRY_CONFLICT')
            for path, axis in [(p.row_path,'y'),(p.column_path,'x')]:
                for parent, child in zip(path,path[1:]):
                    a,b = cells[parent].bbox,cells[child].bbox
                    if overlap(a,b,axis) < .85*((b.y1-b.y0) if axis=='y' else (b.x1-b.x0)):
                        errors.append('PARENT_GEOMETRY_CONFLICT')
        checked.append(CheckedLink(proposal=p, status='unresolved' if errors else 'supported_candidate',
            errors=tuple(sorted(set(errors))), original_text=cells[p.cell_id].text if p.cell_id in cells else None))
    return checked


def geometry_options(region):
    """Finite ID-only choices; no numeric answer or domain vocabulary is consulted.

    Single-level paths only in this pilot. Multilevel raw trees are retained in the
    baseline, not flattened/replaced. Broken physical cells never enter this list.
    """
    complex_tables={c.table_id for c in region.cells if norm(c.text) and ((c.rs or 1)>1 or (c.cs or 1)>1)}
    usable=[c for c in region.cells if c.table_id not in complex_tables
            and c.physical_usable and norm(c.text) and c.rs==c.cs==1]
    options=[]
    for v in usable:
        left=[c for c in usable if c.id!=v.id and c.table_id==v.table_id
              and c.bbox.x1<=v.bbox.x0+1.5 and overlap(c.bbox,v.bbox,'y')>=.85*(v.bbox.y1-v.bbox.y0)]
        if not left:
            continue
        # Flat grid stubs are the leftmost cell of a row, not the preceding value.
        left.sort(key=lambda c:c.bbox.x0)
        row=left[0]
        heads=[c for c in usable if c.bbox.y1<=v.bbox.y0+1.5 and band_match(c.bbox,v.bbox)]
        local=[c for c in heads if c.table_id==v.table_id]
        if local:
            heads=local
        elif not region.source_table_id:
            continue
        if not heads:
            continue
        # Earliest aligned header hypothesis; model may reject it. Later body cells
        # are never offered as a replacement header just because they are nearby.
        heads.sort(key=lambda c:c.bbox.y0)
        head=heads[0]
        options.append(AxisLink(cell_id=v.id,row_path=[row.id],column_path=[head.id],
            row_axis='present',column_axis='present',reason='geometry_option_pending_role_selection'))
    return options


def structured_views(region, checked, baselines):
    """Materialize candidate trees using the existing StructuredTableIR contract.

    Baseline diagnostics remain visible. New views do not grant search/fact approval.
    Header references can cross fragments only inside this recorded logical region.
    """
    from doc_understanding.parser.structure.models import StructuredTableIR, HierarchyNode, CellPath, CellRole
    from doc_understanding.domain.models import Issue
    cells={c.id:c for c in region.cells}
    accepted=[c for c in checked if c.status=='supported_candidate']
    output=[]
    for baseline in baselines:
        original=StructuredTableIR.model_validate(baseline)
        changes=[c for c in accepted if cells[c.proposal.cell_id].table_id==original.table_id]
        if not changes:
            output.append(original)
            continue
        rows={n.node_id:n for n in original.row_tree}
        cols={n.node_id:n for n in original.column_tree}
        mappings={m.cell_id:m for m in original.cell_mappings}
        roles={r.cell_id:r for r in original.cell_roles}
        panel=original.panels[0].panel_id
        def nodes(path,axis):
            target=rows if axis=='row' else cols
            result=[]
            for cid in path:
                c=cells[cid]
                existing=next((n for n in target.values() if n.source_cell_id==cid),None)
                nid=existing.node_id if existing else cid+'/axis/'+axis
                target[nid]=HierarchyNode(node_id=nid,axis=axis,panel_id=panel,label_raw=c.text,
                    parent_id=result[-1] if result else None,source_cell_id=cid,bbox=c.bbox,
                    row_range=(c.row,c.row+c.rs),column_range=(c.col,c.col+c.cs),
                    physical_evidence='inferred',evidence_state='inferred')
                result.append(nid)
                # Do not add a foreign physical cell to this table's cell_roles.
                if c.table_id==original.table_id:
                    roles[cid]=CellRole(cell_id=cid,role='row_label' if axis=='row' else 'column_header',evidence_state='inferred')
            return tuple(result)
        for change in changes:
            p=change.proposal;c=cells[p.cell_id]
            rp=nodes(p.row_path,'row');cp=nodes(p.column_path,'column')
            mappings[c.id]=CellPath(cell_id=c.id,bbox=c.bbox,original_text=c.text,panel_id=panel,
                row_path=rp,column_path=cp,evidence_state='inferred')
            roles[c.id]=CellRole(cell_id=c.id,role='value',evidence_state='inferred')
        # Remove stale header interpretations of cells now explicitly mapped as values.
        changed_values={c.proposal.cell_id for c in changes}
        rows={k:n for k,n in rows.items() if n.source_cell_id not in changed_values}
        cols={k:n for k,n in cols.items() if n.source_cell_id not in changed_values}
        updated=original.model_copy(update=dict(row_tree=tuple(rows.values()),column_tree=tuple(cols.values()),
            cell_roles=tuple(roles.values()),cell_mappings=tuple(mappings.values()),
            issues=original.issues+(Issue(code='AXIS_OVERLAY_CANDIDATE',severity='info',
                message='Source-checked candidate view; baseline ambiguities retained, not semantic approval. Region: '+region.id),),
            analysis_state='review_required'))
        output.append(StructuredTableIR.model_validate(updated.model_dump()))
    return output
