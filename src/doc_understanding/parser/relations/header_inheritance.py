"""Source-bound header borrowing across page fragments; physical cells stay separate.

Acceptance requires page-edge alignment AND a continuing ordinal column. Geometry
alone only creates a candidate. 'accepted' is a rule decision, not human approval.
The stored target tree contains its own row nodes; borrowed columns live in links.
"""
import re

from doc_understanding.domain.models import Issue
from doc_understanding.parser.relations.logical_tables import (
    LogicalCellPath, LogicalTableLink, aligned, contains, norm, text_between,
)
from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer
from doc_understanding.parser.structure.layout import TableLayout
from doc_understanding.parser.structure.trees import path_for

VERSION = 'header-inheritance/1.0'


def flat_grid(table):
    """No repaired merges, unresolved cells, overlapping or missing slots."""
    cells=table.cells
    return (len(cells)==len(table.rows)*len(table.columns)
            and all(c.status!='unresolved' and c.rowspan==c.colspan==1
                    and c.occupied_slots==((c.row_index,c.column_index),) for c in cells)
            and {(c.row_index,c.column_index) for c in cells}
                =={(r,c) for r in range(len(table.rows)) for c in range(len(table.columns))})


def serials(table, header, minimum=3):
    values=sorted((c for c in table.cells if c.column_index==0 and c.row_index>=header),key=lambda c:c.row_index)
    if len(values)<minimum or any(not re.fullmatch(r'[0-9]+',norm(c.original_text)) for c in values):
        return None
    nums=[int(norm(c.original_text)) for c in values]
    return nums if all(b==a+1 for a,b in zip(nums,nums[1:])) else None


def kind(text):
    value=norm(text)
    if not value:return None
    return 'number_or_date' if re.fullmatch(r'[\d.,+\-−/%():]+',value) else 'text'


def pair_reasons(source,target,pages,source_header):
    reasons=[]
    before,after=pages[source.page_number],pages[target.page_number]
    if not aligned(source,target):reasons.append('COLUMN_BOUNDARIES_DIFFER')
    if source.bounding_box.y1<before.height*.85:reasons.append('SOURCE_NOT_AT_PAGE_END')
    if target.bounding_box.y0>after.height*.15:reasons.append('TARGET_NOT_AT_PAGE_START')
    if (text_between(before,source.bounding_box.y1,before.height,source.bounding_box.x0,source.bounding_box.x1)
            or text_between(after,0,target.bounding_box.y0,target.bounding_box.x0,target.bounding_box.x1)):
        reasons.append('INTERVENING_TEXT')
    if not flat_grid(source) or not flat_grid(target):
        return (*reasons, 'NON_FLAT_OR_UNRESOLVED_GRID')
    short_tail = len(target.rows) == 2
    a,b=serials(source,source_header),serials(target,0,minimum=2 if short_tail else 3)
    if a is None or b is None or a[-1]+1!=b[0]:reasons.append('ORDINAL_SEQUENCE_NOT_CONTINUOUS')
    # Compare independent value columns too; a running number alone is insufficient.
    contrasted=0
    for col in range(1,len(source.columns)):
        left={kind(c.original_text) for c in source.cells if c.column_index==col and c.row_index>=source_header}
        right={kind(c.original_text) for c in target.cells if c.column_index==col}
        left.discard(None);right.discard(None)
        if len(left)==len(right)==1 and left==right:contrasted+=1
        elif left and right and left!=right:reasons.append('BODY_COLUMN_TYPES_DIFFER');break
    if contrasted<2:reasons.append('INSUFFICIENT_BODY_COLUMN_CONTRAST')
    if short_tail:
        # Two rows provide less sequence evidence. Require two independently
        # typed, fully populated columns that also agree with the source rows.
        consistent_types = set()
        for col in range(1, len(source.columns)):
            texts = [norm(c.original_text) for table, skip in ((source, source_header), (target, 0))
                     for c in table.cells if c.column_index == col and c.row_index >= skip]
            types = {kind(text) for text in texts}
            if len(types) == 1 and all(any(ch.isalnum() for ch in text) for text in texts):
                consistent_types.update(types)
        if consistent_types != {'text', 'number_or_date'}:
            reasons.append('SHORT_TAIL_TYPES_UNSUPPORTED')
    return tuple(reasons)


def inherit(ir, physical, structured, links=()):
    """Return new structures and source links, preserving unresolved candidates."""
    pages={p.page_number:p for p in ir.pages}
    tables={t.table_id:t for t in structured}
    physical_by_id={t.table_id:t for t in physical}
    linked={l.target_table_id:l for l in links}
    top=[t for t in physical if not any(o.table_id!=t.table_id and o.page_number==t.page_number
         and contains(o.bounding_box,t.bounding_box) for o in physical)]
    top.sort(key=lambda t:(t.page_number,t.bounding_box.y0,t.table_id))
    for target in top:
        # Only absent/ambiguous headers are eligible. Reprinted or different headers remain local.
        current=tables[target.table_id]
        if not any(a.code=='HEADER_REGION_AMBIGUOUS' for a in current.ambiguities):continue
        sources=[s for s in top if s.page_number+1==target.page_number and len(s.columns)==len(target.columns)]
        if not sources:continue
        aligned_sources=[s for s in sources if aligned(s,target)]
        # An aligned fragment ending in the middle of the preceding page cannot
        # be the page-edge predecessor. Keep all competing edge fragments.
        at_end = [s for s in aligned_sources if s.bounding_box.y1 >= pages[s.page_number].height * .85]
        pool=sorted(at_end or aligned_sources or sources,key=lambda t:(-t.bounding_box.y1,t.table_id))
        source=pool[0]
        prev=tables[source.table_id]
        prior=linked.get(source.table_id)
        origin=(next((s for s in structured if any(n.source_cell_id in prior.header_source_cell_ids for n in s.column_tree)),None)
                if prior and prior.status=='accepted' else prev)
        nodes=origin.column_tree if origin else ()
        reasons=list(pair_reasons(source,target,pages,prev.panels[0].header_rows if len(prev.panels)==1 else 1))
        if len(pool)>1:reasons.append('COMPETING_PREDECESSORS')
        if (origin is None or len(origin.panels)!=1 or origin.panels[0].header_rows!=1
                or origin.panels[0].stub_columns!=1 or not nodes
                or any(n.evidence_state=='unresolved' or not norm(n.label_raw) for n in nodes)):
            reasons.append('SOURCE_HEADER_UNRESOLVED')
        if len(current.panels)!=1 or current.panels[0].stub_columns!=1:reasons.append('TARGET_LAYOUT_UNSUPPORTED')
        accepted=not reasons
        link_id=source.table_id+'/header-to/'+target.table_id
        heads=tuple(dict.fromkeys(n.source_cell_id for n in nodes))
        if not accepted:
            # Keep old detached/group proposals where present; never promote them implicitly.
            if target.table_id not in linked:
                linked[target.table_id]=LogicalTableLink(link_id=link_id,source_table_id=source.table_id,
                    target_table_id=target.table_id,kind='page_continuation',header_source_cell_ids=heads,
                    inherited_row_source_cell_ids=(),paths=(),reason_codes=tuple(dict.fromkeys(reasons)),
                    candidate_source_table_ids=tuple(t.table_id for t in pool[1:]),algorithm=VERSION)
            continue
        fresh=SemanticTableAnalyzer().analyze(target,source_page=pages[target.page_number],
            layout=TableLayout.uniform(len(target.rows),0,1))
        row_nodes={n.node_id:n for n in fresh.row_tree}
        paths=[]
        for m in fresh.cell_mappings:
            c=next(c for c in target.cells if c.cell_id==m.cell_id)
            leaves=[n for n in nodes if n.column_range==(c.column_index,c.column_index+1)
                    and not any(o.parent_id==n.node_id for o in nodes)]
            column=path_for(leaves[0],nodes) if len(leaves)==1 else ()
            by_id={n.node_id:n for n in nodes}
            paths.append(LogicalCellPath(cell_id=m.cell_id,
                row_source_cell_ids=tuple(row_nodes[n].source_cell_id for n in m.row_path),
                column_source_cell_ids=tuple(by_id[n].source_cell_id for n in column),
                state='inferred' if column and m.evidence_state!='unresolved' else 'unresolved',
                reasons=('ALIGNED_PAGE_EDGES','CONTINUOUS_ORDINALS','MATCHING_BODY_TYPES','SOURCE_HEADER_SUPPORTED')))
        linked[target.table_id]=LogicalTableLink(link_id=link_id,source_table_id=source.table_id,
            target_table_id=target.table_id,kind='page_continuation',header_source_cell_ids=heads,
            inherited_row_source_cell_ids=(),paths=tuple(paths),status='accepted',
            reason_codes=('ALIGNED_PAGE_EDGES','CONTINUOUS_ORDINALS','MATCHING_BODY_TYPES','SOURCE_HEADER_SUPPORTED'),algorithm=VERSION)
        # Bare target mappings cannot be used without their accepted link.
        tables[target.table_id]=fresh.model_copy(update=dict(
            cell_mappings=tuple(m.model_copy(update={'evidence_state':'unresolved'}) for m in fresh.cell_mappings),
            issues=fresh.issues+(Issue(code='BORROWED_HEADER_REQUIRED',severity='info',message=link_id,
                                      source_references=target.source_references),)))
    ordered=sorted(linked.values(),key=lambda l:(physical_by_id[l.target_table_id].page_number,
                                                physical_by_id[l.target_table_id].bounding_box.y0,l.link_id))
    return tuple(tables[t.table_id] for t in structured),tuple(ordered)


def effective_structure(doc,table_id):
    """Read projection: original column nodes + local row nodes, link-capped state.

    No text, cells or nodes are invented; the borrowed nodes keep original IDs and
    geometry. This projection is not serialized back over the target structure.
    """
    st=next(t for t in doc.table_structure if t.table_id==table_id)
    link=next((l for l in doc.logical_table_links if l.target_table_id==table_id
               and l.status=='accepted' and l.algorithm==VERSION),None)
    if link is None:return st
    source={n.source_cell_id:n for t in doc.table_structure for n in t.column_tree}
    borrowed=tuple(source[c] for c in link.header_source_cell_ids if c in source)
    if len(borrowed)!=len(link.header_source_cell_ids) or any(n.evidence_state=='unresolved' for n in borrowed):return st
    paths={p.cell_id:p for p in link.paths}
    rows={n.node_id:n for n in st.row_tree}
    mappings=[]
    for m in st.cell_mappings:
        p=paths.get(m.cell_id)
        good=p and p.state=='inferred' and tuple(rows[n].source_cell_id for n in m.row_path)==p.row_source_cell_ids
        if good and all(cid in source for cid in p.column_source_cell_ids):
            m=m.model_copy(update=dict(column_path=tuple(source[c].node_id for c in p.column_source_cell_ids),evidence_state='inferred'))
        mappings.append(m)
    return st.model_copy(update=dict(column_tree=borrowed,cell_mappings=tuple(mappings)))
