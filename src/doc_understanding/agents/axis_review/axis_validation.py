# 역할: 출처 포함 여부·역할 일관성·헤더 근거·매핑 누락을 독립적으로 검사한다.
"""Source coverage, role consistency and independent header evidence gates.

No source mutation, model call, document vocabulary or semantic certification.
This validator checks proposals; it does not silently invent missing paths.
"""
from collections import Counter, defaultdict
from statistics import median
from typing import Literal
import re

from doc_understanding.domain.models import EvidenceModel
from doc_understanding.agents.axis_review.axis_resolver import AxisRegion, AxisProposal, CheckedLink, band_match, check_proposal, norm, rule_proposal


class Finding(EvidenceModel):
    code: str
    cell_ids: tuple[str, ...]


class HeaderEvidence(EvidenceModel):
    header_cell_id: str
    kind: Literal['numeric_body_matrix', 'source_appearance_contrast', 'compact_band_body_contrast']
    source_cell_ids: tuple[str, ...]
    source_span_ids: tuple[str, ...]


class CellAudit(EvidenceModel):
    cell_id: str
    text_raw: str
    span_ids: tuple[str, ...]
    proposed_roles: tuple[str, ...]
    disposition: Literal['candidate_value', 'rejected_value', 'axis_reference',
                         'explicit_abstention', 'unaccounted', 'blank']
    physical_usable: bool
    issues: tuple[str, ...]


class AxisAudit(EvidenceModel):
    schema_version: Literal['axis-validation/1.0'] = 'axis-validation/1.0'
    region_id: str
    checked_links: tuple[CheckedLink, ...]
    header_evidence: tuple[HeaderEvidence, ...]
    cells: tuple[CellAudit, ...]
    findings: tuple[Finding, ...]
    semantic_verified: Literal[False] = False
    facts_published: Literal[0] = 0


class MappingCoverage(EvidenceModel):
    cell_id: str
    text_raw: str
    roles: tuple[str, ...]
    mapping_count: int
    has_both_paths: bool
    issues: tuple[str, ...]


def audit_mapping_coverage(region, tables):
    """Observe baseline/new IR independently of whether an overlay proposed a link.

    A numeric row identifier can be legitimate, but dropping its value role is not
    treated as complete coverage. This audit reports; it does not rewrite that tree.
    """
    roles=defaultdict(set);mappings=defaultdict(list)
    for t in tables:
        for r in t.get('cell_roles',[]):roles[r['cell_id']].add(r['role'])
        for n in t.get('row_tree',[]):roles[n['source_cell_id']].add('row_label')
        for n in t.get('column_tree',[]):roles[n['source_cell_id']].add('column_header')
        for m in t.get('cell_mappings',[]):mappings[m['cell_id']].append(m)
    rows=[]
    for c in region.cells:
        ms=mappings[c.id];rs=roles[c.id];codes=[]
        if len(ms)>1:codes.append('DUPLICATE_VALUE_MAPPING')
        if norm(c.text) and not ms and (not rs or rs <= {'unknown','value'}):codes.append('VALUE_OR_ROLE_UNMAPPED')
        if numeric_value(c.text) and 'row_label' in rs and not ms:codes.append('NUMERIC_AXIS_VALUE_UNMAPPED')
        if 'value' in rs and rs & {'row_label','column_header'}:codes.append('ROLE_CONFLICT')
        if ms and not any(m['row_path'] and m['column_path'] for m in ms):codes.append('PATH_UNRESOLVED')
        rows.append(MappingCoverage(cell_id=c.id,text_raw=c.text,roles=tuple(sorted(rs)),mapping_count=len(ms),
            has_both_paths=any(m['row_path'] and m['column_path'] for m in ms),issues=tuple(codes)))
    return tuple(rows)


def numeric_value(text):
    """Numeric data, including currency suffixes; not numbered text labels/years."""
    return bool(re.fullmatch(r'[+\-△▲]?\d[\d,.]*(?:%|‰|원|달러|엔|유로|USD|EUR|JPY)?', norm(text)))


def label_like(text):
    value=norm(text)
    return bool(value) and len(value)<=40 and not numeric_value(value) and not re.search(
        r'(?:합니다|됩니다|있습니다|없습니다|하십시오)[.!。]?$',value)


def header_evidence(region):
    """Evidence independent of the submitted model selections and their reasons.

    Compact-band heuristic: >=2 short distinct labels, >=2 aligned body rows,
    textual left stubs, and right body text median >=2x header max length.
    It is weak positive evidence, never proof of semantics; unsupported tables hold.
    """
    cells={c.id:c for c in region.cells};proof={}
    def add(h,kind,refs):
        if not h.physical_usable or not label_like(h.text):return
        ids=tuple(dict.fromkeys([h.id]+list(refs)))
        proof.setdefault(h.id,HeaderEvidence(header_cell_id=h.id,kind=kind,
            source_cell_ids=ids,source_span_ids=tuple(dict.fromkeys(s for i in ids for s in cells[i].spans))))
    rules=rule_proposal(region)
    for p in rules.links:
        for hid in p.column_path:
            kind='numeric_body_matrix' if p.reason.startswith('flat_matrix') else 'source_appearance_contrast'
            supporters=[x.cell_id for x in rules.links if hid in x.column_path]
            add(cells[hid],kind,supporters+p.row_path)
    tables=defaultdict(list)
    for c in region.cells:tables[c.table_id].append(c)
    usable=[c for c in region.cells if c.physical_usable and norm(c.text) and c.rs==c.cs==1]
    for tid,all_cells in tables.items():
        if any(norm(c.text) and ((c.rs or 1)>1 or (c.cs or 1)>1) for c in all_cells):continue
        local=[c for c in usable if c.table_id==tid]
        if not local:continue
        first_y=min(c.bbox.y0 for c in local)
        heads=sorted([c for c in local if abs(c.bbox.y0-first_y)<=1.5],key=lambda c:c.bbox.x0)
        if len(heads)<2 or any(not label_like(c.text) or len(norm(c.text))>12 for c in heads):continue
        if len({norm(c.text) for c in heads})!=len(heads):continue
        rows=defaultdict(list)
        for c in usable:
            if c.bbox.y0<max(h.bbox.y1 for h in heads)-1.5:continue
            if c.table_id!=tid and not region.source_table_id:continue
            rows[(c.table_id,c.row)].append(c)
        body=[]
        for row in rows.values():
            aligned=[]
            for h in heads:
                matches=[c for c in row if band_match(h.bbox,c.bbox)]
                if len(matches)!=1:break
                aligned.append(matches[0])
            if len(aligned)==len(heads) and not numeric_value(aligned[0].text):body.append(aligned)
        if len(body)<2:continue
        header_length=max(len(norm(c.text)) for c in heads)
        if median(len(norm(row[-1].text)) for row in body)<2*header_length:continue
        for h in heads[1:]:add(h,'compact_band_body_contrast',[c.id for c in heads]+[c.id for row in body for c in row])
    return tuple(proof.values())


def audit_region(region: AxisRegion, proposal: AxisProposal) -> AxisAudit:
    cells={c.id:c for c in region.cells};issues=defaultdict(set);roles=defaultdict(set)
    duplicate_ids=[i for i,n in Counter(c.id for c in region.cells).items() if n>1]
    findings=[]
    if duplicate_ids:findings.append(Finding(code='DUPLICATE_SOURCE_CELL_ID',cell_ids=tuple(duplicate_ids)))
    unknown_abstain=sorted(set(proposal.abstain_cell_ids)-cells.keys())
    if unknown_abstain:findings.append(Finding(code='UNKNOWN_ABSTENTION_ID',cell_ids=tuple(unknown_abstain)))
    for p in proposal.links:
        roles[p.cell_id].add('value')
        for i in p.row_path:roles[i].add('row_label')
        for i in p.column_path:roles[i].add('column_header')
    for i,rs in roles.items():
        if len(rs)>1:issues[i].add('ROLE_CONFLICT')
        if i in cells and 'row_label' in rs and 'value' not in rs and numeric_value(cells[i].text):
            issues[i].add('NUMERIC_AXIS_VALUE_UNMAPPED')
    proofs=header_evidence(region);supported_headers={p.header_cell_id for p in proofs}
    checked=[]
    for c in check_proposal(region,proposal):
        p=c.proposal;errors=set(c.errors)
        for i in [p.cell_id]+p.row_path+p.column_path:errors.update(issues[i])
        if duplicate_ids:errors.add('DUPLICATE_SOURCE_CELL_ID')
        if any(h not in supported_headers for h in p.column_path):errors.add('HEADER_EVIDENCE_MISSING')
        checked.append(c.model_copy(update={'errors':tuple(sorted(errors)),
            'status':'unresolved' if errors else 'supported_candidate'}))
        issues[p.cell_id].update(errors)
    accepted={c.proposal.cell_id for c in checked if c.status=='supported_candidate'}
    proposed={p.cell_id for p in proposal.links};abstained=set(proposal.abstain_cell_ids)
    # Account for every source cell. Abstention is explicit coverage, not successful mapping.
    inventory=[]
    for c in region.cells:
        rs=roles[c.id]
        if not norm(c.text):disposition='blank'
        elif c.id in proposed:disposition='candidate_value' if c.id in accepted else 'rejected_value'
        elif rs:disposition='axis_reference'
        elif c.id in abstained:disposition='explicit_abstention'
        else:disposition='unaccounted';issues[c.id].add('UNACCOUNTED_CELL')
        if norm(c.text) and not c.physical_usable:issues[c.id].add('PHYSICAL_OR_SPAN_EVIDENCE_UNRESOLVED')
        if norm(c.text) and not rs:issues[c.id].add('ROLE_UNRESOLVED')
        for hid in supported_headers:
            h=cells[hid]
            if (c.id!=hid and norm(c.text) and c.bbox.y0>=h.bbox.y1-1.5 and band_match(c.bbox,h.bbox)
                    and (c.table_id==h.table_id or region.source_table_id) and c.id not in accepted):
                issues[c.id].add('UNMAPPED_VALUE_CANDIDATE')
        inventory.append(CellAudit(cell_id=c.id,text_raw=c.text,span_ids=c.spans,
            proposed_roles=tuple(sorted(rs)),disposition=disposition,physical_usable=c.physical_usable,
            issues=tuple(sorted(issues[c.id]))))
    grouped=defaultdict(list)
    for i,codes in issues.items():
        for code in codes:grouped[code].append(i)
    findings.extend(Finding(code=code,cell_ids=tuple(sorted(set(ids)))) for code,ids in sorted(grouped.items()))
    return AxisAudit(region_id=region.id,checked_links=tuple(checked),header_evidence=proofs,
                     cells=tuple(inventory),findings=tuple(findings))


def validated_views(region, audit, baselines):
    """New candidate revision: rejected paths cannot leak back via a baseline mapping.

    Preserve original values, nodes and baseline diagnostics as review evidence. Only
    the new revision clears paths for rejected targets (or use of a rejected header).
    The audit remains mandatory: untouched baseline mappings are not newly certified.
    """
    from doc_understanding.domain.models import Issue
    from doc_understanding.agents.axis_review.axis_resolver import structured_views
    rejected={c.proposal.cell_id for c in audit.checked_links if c.status=='unresolved'}
    rejected_heads={h for c in audit.checked_links if 'HEADER_EVIDENCE_MISSING' in c.errors
                    for h in c.proposal.column_path if h not in {e.header_cell_id for e in audit.header_evidence}}
    output=[]
    for view in structured_views(region,audit.checked_links,baselines):
        nodes={n.node_id:n for n in view.row_tree+view.column_tree};held=[];mappings=[]
        for m in view.cell_mappings:
            bad=m.cell_id in rejected or any(nodes[n].source_cell_id in rejected_heads
                                            for n in m.row_path+m.column_path if n in nodes)
            if bad:
                held.append(m.cell_id)
                m=m.model_copy(update={'row_path':(),'column_path':(),'evidence_state':'unresolved'})
            mappings.append(m)
        if held:
            view=view.model_copy(update={'cell_mappings':tuple(mappings),'analysis_state':'review_required',
                'issues':view.issues+(Issue(code='AXIS_VALIDATION_WITHHELD',severity='warning',
                    message='New revision withheld paths; original values/nodes/baseline retained. Cells: '+', '.join(held)),)})
        output.append(view)
    return output
