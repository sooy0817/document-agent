# 역할: 원본 셀과 선 근거를 유지하면서 바깥 표와 중첩 표의 분리 후보를 만든다.
"""Bounded, source-backed nested-region hypotheses; no document-specific rules.

The original flat result is immutable. Only native child candidates with a
unique enclosing native cell and prose outside the child are eligible. A
failed reconstruction leaves the entire affected flat region unchanged.
"""
from doc_understanding.parser.physical.geometry import close_box, overlap
from doc_understanding.parser.physical.physical_grid import PhysicalGridReconstructor
from doc_understanding.parser.physical.region_detector import TableRegion
from doc_understanding.parser.physical.region_assessor import assess_region
from doc_understanding.domain.models import Issue


# 상자의 너비와 높이로 면적을 계산한다. 포함 관계 판정에 사용한다.
def area(b):
    return (b.x1-b.x0)*(b.y1-b.y0)


# 자식 상자가 부모보다 충분히 작고 허용 오차 안에 포함되는지 검사한다.
def contains(child, parent, tol):
    return (0 < area(child) < .95*area(parent)
            and child.x0 >= parent.x0-tol and child.y0 >= parent.y0-tol
            and child.x1 <= parent.x1+tol and child.y1 <= parent.y1+tol)


# 영역 안의 선을 선택하고 넘친 구간만 자른 복사본을 만든다. 원본 선은 수정하지 않는다.
def scoped(lines, box, tol):
    result=[]
    for line in lines:
        low,high,start,end=((box.y0,box.y1,box.x0,box.x1) if line.axis=='horizontal'
                            else (box.x0,box.x1,box.y0,box.y1))
        if not low-tol <= line.position <= high+tol:
            continue
        a,b=max(start,line.start),min(end,line.end)
        if b-a <= tol:
            continue
        result.append(line if a==line.start and b==line.end else line.model_copy(update={
            'start':a,'end':b,'line_id':f'{line.line_id}/clip-{a:.3f}-{b:.3f}'}))
    return tuple(result)


# 자식 내부에만 속한 선 구간을 부모 분석에서 제외한다. 부모와 공유하는 외곽선은 유지한다.
def without_child(lines, child, parent_cell, tol):
    """Remove only child-local intervals. Shared parent edges keep their evidence."""
    result=[]
    for line in lines:
        low,high,a,b,outer=((child.y0,child.y1,child.x0,child.x1,(parent_cell.y0,parent_cell.y1))
            if line.axis=='horizontal' else (child.x0,child.x1,child.y0,child.y1,(parent_cell.x0,parent_cell.x1)))
        if (not low-tol <= line.position <= high+tol
                or any(abs(line.position-x)<=tol for x in outer)
                or min(line.end,b)<=max(line.start,a)):
            result.append(line);continue
        for start,end in [(line.start,min(line.end,a)),(max(line.start,b),line.end)]:
            if end-start>tol:
                result.append(line.model_copy(update={'start':start,'end':end,
                    'line_id':f'{line.line_id}/remaining-{start:.3f}-{end:.3f}'}))
    return tuple(result)


# 중첩 재구성 결과를 partial로 표시하고 확정 셀도 inferred로 낮춰 후보임을 명시한다.
def mark_candidate(table):
    return table.model_copy(update={
        'analysis_state':'partial',
        'cells':tuple(c.model_copy(update={'status':'inferred'}) if c.status=='resolved' else c for c in table.cells),
        'validation_issues':table.validation_issues+(Issue(code='NESTED_REGION_HYPOTHESIS',severity='warning',
            message='Scoped parent/child geometry; semantic interpretation remains unvalidated.'),)})


# 원본 셀 조각들이 복원 셀 너비를 완전히 분할하고 비공란 span을 정확히 보존하는지 검사한다.
def native_partition_support(native, rebuilt, nonempty, tol):
    """Accept native horizontal padding fragments only as a complete partition.

    A physical cell may correspond to several native cells, but every native
    rectangle must belong to exactly one physical cell, tile its full width,
    and preserve that cell's exact nonempty span membership.
    """
    groups = {c.cell_id: [] for c in rebuilt.cells}
    for n in native.cells:
        b = n.source.bbox
        matches = [c for c in rebuilt.cells if
            abs(c.source_bbox.y0-b.y0)<=tol and abs(c.source_bbox.y1-b.y1)<=tol
            and c.source_bbox.x0-tol<=b.x0 and b.x1<=c.source_bbox.x1+tol]
        if len(matches)!=1:
            return None
        groups[matches[0].cell_id].append(n)
    support = []
    for c in rebuilt.cells:
        parts = sorted(groups[c.cell_id], key=lambda n:n.source.bbox.x0)
        if not parts:
            return None
        b = c.source_bbox
        if (abs(parts[0].source.bbox.x0-b.x0)>tol or abs(parts[-1].source.bbox.x1-b.x1)>tol
                or any(abs(a.source.bbox.x1-z.source.bbox.x0)>tol for a,z in zip(parts,parts[1:]))):
            return None
        if ({s for n in parts for s in n.span_refs}&nonempty) != (set(c.span_refs)&nonempty):
            return None
        support.append({'physical_cell_id':c.cell_id,'native_cell_ids':[n.cell_id for n in parts]})
    return support


# 중첩 후보의 부모·자식 선을 분리해 새 페이지 결과와 감사 기록을 반환한다.
# 입력 IR은 변경하지 않으며 경쟁하거나 근거가 부족한 후보는 적용하지 않는다.
def split_nested_regions(document_id, page, flat, max_candidates=16):
    """Return a new page result plus an audit sidecar, never mutating original IR."""
    log={'schema_version':'nested-region-audit/1.0','algorithm':'native-scoped/2','page':page.page_number,
         'route':'skip_no_candidate','candidates':[],'applied':0}
    if len(page.tables)<2:
        return flat,log
    tol=1.5
    spans={s.span_id:s for b in page.blocks for s in b.spans}
    nonempty={sid for sid,s in spans.items() if s.text_raw.strip()}
    candidates=[]
    for child in page.tables:
        if len(child.cells)<2:
            continue
        cb=child.source.bbox
        parents=[(t,c) for t in page.tables if t.table_candidate_id!=child.table_candidate_id
                 for c in t.cells if contains(cb,c.source.bbox,tol)]
        if not parents:
            continue
        log['route']='process_candidates'
        rec={'child_native_table_id':child.table_candidate_id,
             'parent_candidate_ids':[c.cell_id for _,c in parents],
             'status':'unresolved','reasons':[]}
        log['candidates'].append(rec)
        winners=[(t,c) for t,c in parents if all(c.cell_id==o.cell_id or contains(c.source.bbox,o.source.bbox,tol) for _,o in parents)]
        if len(winners)!=1:
            rec['reasons']=['COMPETING_PARENT_CELLS'];continue
        parent_table,parent=winners[0];pb=parent.source.bbox
        child_spans={s for c in child.cells for s in c.span_refs}&nonempty
        outside=(set(parent.span_refs)&nonempty)-child_spans
        rec.update(parent_native_cell_id=parent.cell_id,parent_native_table_id=parent_table.table_candidate_id)
        if not child_spans or not child_spans<=set(parent.span_refs):
            rec['reasons']=['CHILD_SPAN_SUPPORT_INVALID'];continue
        inset = cb.x0>pb.x0+tol and cb.x1<pb.x1-tol
        shared_left = abs(cb.x0-pb.x0)<=tol and cb.x1<pb.x1-tol
        shared_right = abs(cb.x1-pb.x1)<=tol and cb.x0>pb.x0+tol
        outside_vertical = [sid for sid in outside if sid in spans and
            (spans[sid].source.bbox.y1<=cb.y0+tol or spans[sid].source.bbox.y0>=cb.y1-tol)]
        # A shared side is eligible only with a distinct inset opposite side and
        # source prose above/below. Reconstruction below must restore both grids,
        # preserve shared edges, and preserve the exact source span set.
        shared_supported = (shared_left or shared_right) and bool(outside_vertical)
        if not outside or not (inset or shared_supported):
            rec['reasons']=['INDEPENDENT_CHILD_LAYOUT_NOT_ESTABLISHED'];continue
        if not inset:
            rec['layout_evidence']={'rule':'shared_side_with_external_prose',
                'side':'left' if shared_left else 'right','prose_span_ids':sorted(outside_vertical)}
        owners=[t for t in flat.tables if parent_table.table_candidate_id in t.table_candidate_ids
                and overlap(t.bounding_box,pb)>=.95*area(pb)]
        if len(owners)!=1:
            rec['reasons']=['FLAT_PARENT_REGION_AMBIGUOUS'];continue
        owner=owners[0]
        if any(close_box(t.bounding_box,cb,tol) and len(t.cells)>=2 for t in flat.tables):
            rec['status']='already_separate';continue
        candidates.append((child,parent,owner,child_spans,rec))
    if len(log['candidates'])>max_candidates:
        for rec in log['candidates']:
            rec['status']='unresolved';rec['reasons']=['NESTED_CANDIDATE_BUDGET_EXCEEDED']
        return flat,log
    # One flat region is a transaction. Overlapping/deep child hypotheses are
    # preserved for review rather than recursively modifying each other.
    output=list(flat.tables);next_index=max((int(t.table_id.rsplit('physical',1)[1]) for t in output),default=-1)+1
    for owner in flat.tables:
        group=[c for c in candidates if c[2].table_id==owner.table_id]
        if not group:
            continue
        if any(overlap(a[0].source.bbox,b[0].source.bbox)>0 for i,a in enumerate(group) for b in group[i+1:]):
            for *_,rec in group:rec['reasons']=['OVERLAPPING_OR_DEEP_CHILD_HYPOTHESES']
            continue
        policy=owner.policy;engine=PhysicalGridReconstructor(policy);lines=owner.lines;children=[]
        reason=None
        try:
            for offset,(child,parent,_,child_spans,rec) in enumerate(group):
                child_lines=scoped(owner.lines,child.source.bbox,policy.tolerance)
                region=TableRegion(child.source.bbox,child_lines)
                if assess_region(region,page,policy).decision=='rejected':
                    raise ValueError('CHILD_REGION_REJECTED')
                rebuilt=engine._region(document_id,page,next_index+offset,region)
                if rebuilt.analysis_state=='invalid' or len(rebuilt.cells)<2 or any(c.status=='unresolved' for c in rebuilt.cells):
                    raise ValueError('CHILD_GRID_UNRESOLVED')
                if any(not any(close_box(c.source_bbox,n.source.bbox,tol) for c in rebuilt.cells) for n in child.cells):
                    support = native_partition_support(child,rebuilt,nonempty,tol)
                    if support is None:
                        raise ValueError('CHILD_NATIVE_GEOMETRY_DISAGREEMENT')
                    rec['native_partition_support'] = support
                actual={s for c in rebuilt.cells for s in c.span_refs}&nonempty
                if actual!=child_spans:
                    raise ValueError('CHILD_TEXT_COVERAGE_CONFLICT')
                children.append(rebuilt)
                lines=without_child(lines,child.source.bbox,parent.source.bbox,policy.tolerance)
            rebuilt_parent=engine._region(document_id,page,int(owner.table_id.rsplit('physical',1)[1]),
                                          TableRegion(owner.bounding_box,lines))
            if rebuilt_parent.analysis_state=='invalid':
                raise ValueError('PARENT_GRID_INVALID')
            for child,parent,_,_,rec in group:
                matches=[c for c in rebuilt_parent.cells if close_box(c.source_bbox,parent.source.bbox,tol)]
                if len(matches)!=1 or matches[0].status=='unresolved':
                    raise ValueError('PARENT_CELL_NOT_RESTORED')
                rec['parent_physical_cell_id']=matches[0].cell_id
            before={s for c in owner.cells for s in c.span_refs}
            after={s for t in [rebuilt_parent,*children] for c in t.cells for s in c.span_refs}
            if before!=after:
                raise ValueError('PAGE_TEXT_COVERAGE_CHANGED')
            # Unrelated cells must retain their geometry and original strings.
            for cell in owner.cells:
                if any(overlap(cell.source_bbox,p.source.bbox)>policy.tolerance**2 for _,p,*_ in group):
                    continue
                same=[c for c in rebuilt_parent.cells if close_box(c.source_bbox,cell.source_bbox,policy.tolerance)]
                if len(same)!=1 or same[0].original_text!=cell.original_text:
                    raise ValueError('UNRELATED_CELL_CHANGED')
        except ValueError as exc:
            reason=str(exc)
        if reason:
            for *_,rec in group:
                rec.pop('parent_physical_cell_id',None);rec['reasons']=[reason]
            continue
        output[output.index(owner)]=mark_candidate(rebuilt_parent)
        output.extend(mark_candidate(t) for t in children)
        for (_,parent,_,_,rec),child in zip(group,children):
            rec.update(status='split_candidate',reasons=[],child_physical_table_id=child.table_id,
                child_line_ids=[l.line_id for l in child.lines],parent_line_ids=[l.line_id for l in rebuilt_parent.lines])
        next_index+=len(children);log['applied']+=len(children)
    return flat.model_copy(update={'tables':tuple(output)}),log
