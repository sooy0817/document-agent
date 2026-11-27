# 역할: 기간 해석에 필요한 원문 범위의 누락 위험을 검사하며 기하만으로 의미를 확정하지 않는다.
"""Source-input coverage guard. Geometry finds risks, never semantic applicability."""
from typing import Literal, TypedDict
from doc_understanding.domain.models import BBox, EvidenceModel
from doc_understanding.parser.assembly.nested_content import contains
from doc_understanding.domain.axis_regions import digest, encloses, area, overlap
from doc_understanding.parser.context.period_decisions import PeriodDecisions, validate_decisions, compare_decisions, build_decision_graph

VERSION='period-coverage/1.1'
EDGE_FRACTION=.12
HORIZONTAL_OVERLAP=.8


# 부모·인접 페이지 등 문맥 유형별 포함/누락/불명 상태와 확인한 원본을 기록한다.
class ContextFinding(EvidenceModel):
    kind: Literal['parent','local_row','previous_page','next_page','source_inventory']
    status: Literal['included','missing','unknown','not_applicable']
    source_cell_ids: list[str]
    missing_span_ids: list[str]
    inspected_pages: list[int]
    reason: str


# 값 셀별로 부족한 span과 문맥 검사 결과를 묶는다.
class TargetCoverage(EvidenceModel):
    target_cell_id: str
    status: Literal['checked_no_gap','missing','unknown']
    missing_span_ids: list[str]
    findings: list[ContextFinding]


# 입력 해시·문서 ID에 연결된 대상별 문맥 피복 결과다. 의미 정답 승인은 포함하지 않는다.
class ContextCoverage(EvidenceModel):
    version: str=VERSION
    input_sha256: str
    document_id: str
    targets: list[TargetCoverage]
    semantic_verified: Literal[False]=False


# 여러 원본 상자를 포함하는 외접 상자를 계산한다.
def _union(boxes):
    return BBox(x0=min(b.x0 for b in boxes),y0=min(b.y0 for b in boxes),
                x1=max(b.x1 for b in boxes),y1=max(b.y1 for b in boxes))


# 페이지 너비로 정규화한 가로 겹침이 기준 이상인지 검사한다.
def _aligned(a,b,width_a,width_b):
    lo=max(a.x0/width_a,b.x0/width_b);hi=min(a.x1/width_a,b.x1/width_b)
    denominator=max(a.x1/width_a-a.x0/width_a,b.x1/width_b-b.x0/width_b)
    return denominator>0 and max(0,hi-lo)/denominator>=HORIZONTAL_OVERLAP


# 모델에 제공한 span을 전체 원본 목록과 비교해 부모·페이지 연결 문맥의 누락 위험을 찾는다.
def audit_context(region,target_ids,document):
    """Compare actual supplied span IDs with full native inventory, per target.

    Parent candidates require strict containment plus shared source spans. Page
    continuation is only a conservative risk: an edge row and aligned edge table
    on its neighbor. No document names, expected counts or domain row names.
    `checked_no_gap` means these bounded checks found no gap, never all-context proof.
    """
    by_id={c.id:c for c in region.cells};provided={s for c in region.cells for s in c.spans}
    pages={p.page_number:p for p in document.pages} if document else {}
    page=pages.get(region.page)
    registry_ok=document is not None and document.document_id==region.document_id and page is not None
    spans={s.span_id:s for p in pages.values() for b in p.blocks for s in b.spans}
    # 필요한 비공란 span과 실제 제공 목록을 비교하고 원본 부재·경쟁 근거는 unknown으로 구분한다.
    def finding(kind,cells,inspected,reason,ambiguous=False):
        required={s for c in cells for s in c.span_refs if s in spans and spans[s].text_raw.strip()}
        missing=sorted(required-provided)
        unavailable=any(s not in spans for c in cells for s in c.span_refs)
        return ContextFinding(kind=kind,status='unknown' if ambiguous or unavailable else 'missing' if missing else 'included',
            source_cell_ids=list(dict.fromkeys(c.cell_id for c in cells)),missing_span_ids=missing,
            inspected_pages=inspected,reason=reason)
    records=[]
    for target in target_ids:
        findings=[];cell=by_id.get(target)
        if not registry_ok or cell is None or any(s not in spans for s in provided):
            findings.append(ContextFinding(kind='source_inventory',status='unknown',source_cell_ids=[],
                missing_span_ids=[],inspected_pages=[],reason='SOURCE_INVENTORY_UNAVAILABLE_OR_MISMATCH'))
        else:
            same_fragment=[c for c in region.cells if c.table_id==cell.table_id]
            fragment_box=_union([c.bbox for c in same_fragment])
            fragment_spans={s for c in same_fragment for s in c.spans}
            parent_owners=[(t,c) for t in page.tables for c in t.cells if contains(fragment_box,c.source.bbox)]
            parents=[(c,bool(fragment_spans.intersection(c.span_refs))) for _,c in parent_owners]
            for parent,shared in parents:
                findings.append(finding('parent',[parent],[region.page],
                    'STRICT_CONTAINMENT_AND_SHARED_SPANS' if shared else 'CONTAINMENT_WITHOUT_SPAN_IDENTITY',not shared))
            if not parents:
                findings.append(ContextFinding(kind='parent',status='not_applicable',source_cell_ids=[],
                    missing_span_ids=[],inspected_pages=[region.page],reason='NO_STRICT_PARENT_IN_NATIVE_INVENTORY'))
            owners=[(t,c) for t in page.tables for c in t.cells if encloses(c.source.bbox,cell.bbox) or
                (bool(cell.spans) and set(c.span_refs)==set(cell.spans) and encloses(cell.bbox,c.source.bbox))]
            owners.sort(key=lambda item:area(item[1].source.bbox))
            row_box=cell.bbox;table_box=fragment_box
            if owners:
                table,owner=owners[0]
                siblings=[c for c in table.cells if overlap(c.source.bbox,owner.source.bbox,'y')>0]
                findings.append(finding('local_row',siblings,[region.page],'NATIVE_ROW_SOURCE_SPAN_COVERAGE'))
                row_box=owner.source.bbox
                table_box=table.source.bbox
            else:
                findings.append(ContextFinding(kind='local_row',status='unknown',source_cell_ids=[],
                    missing_span_ids=[],inspected_pages=[region.page],reason='NO_NATIVE_OWNER_FOR_TARGET'))
            # Use the outer containing cell for edge continuation risk, not the
            # inner numeric slot alone; a parent explanation can cross a page.
            for parent,shared in parents:
                if shared:
                    row_box=_union([row_box,parent.source.bbox])
                    table_box=_union([table_box,next(t.source.bbox for t,c in parent_owners if c.cell_id==parent.cell_id)])
            for direction,kind in [(-1,'previous_page'),(1,'next_page')]:
                number=region.page+direction;neighbor=pages.get(number)
                at_table_edge=abs(row_box.y0-table_box.y0)<=1.5 if direction<0 else abs(row_box.y1-table_box.y1)<=1.5
                near_edge=at_table_edge and (row_box.y0<=page.height*EDGE_FRACTION if direction<0 else row_box.y1>=page.height*(1-EDGE_FRACTION))
                if not near_edge or number<1 or number>len(pages):
                    findings.append(ContextFinding(kind=kind,status='not_applicable',source_cell_ids=[],missing_span_ids=[],
                        inspected_pages=[region.page]+([number] if neighbor else []),
                        reason='TARGET_ROW_NOT_AT_PAGE_EDGE' if not near_edge else 'DOCUMENT_BOUNDARY'))
                    continue
                if neighbor is None or neighbor.text_status=='ocr_required':
                    findings.append(ContextFinding(kind=kind,status='unknown',source_cell_ids=[],missing_span_ids=[],
                        inspected_pages=[region.page],reason='ADJACENT_PAGE_EVIDENCE_UNAVAILABLE'))
                    continue
                candidates=[t for t in neighbor.tables if
                    (t.source.bbox.y1>=neighbor.height*(1-EDGE_FRACTION) if direction<0 else t.source.bbox.y0<=neighbor.height*EDGE_FRACTION)
                    and _aligned(row_box,t.source.bbox,page.width,neighbor.width)]
                if candidates:
                    findings.append(finding(kind,[c for t in candidates for c in t.cells],
                        [region.page,number],'ALIGNED_EDGE_TABLE_CONTINUATION_RISK_NOT_CONFIRMED'))
                else:
                    findings.append(ContextFinding(kind=kind,status='not_applicable',source_cell_ids=[],missing_span_ids=[],
                        inspected_pages=[region.page,number],reason='NO_ALIGNED_ADJACENT_EDGE_TABLE'))
        status='unknown' if any(f.status=='unknown' for f in findings) else 'missing' if any(f.status=='missing' for f in findings) else 'checked_no_gap'
        records.append(TargetCoverage(target_cell_id=target,status=status,
            missing_span_ids=sorted({s for f in findings for s in f.missing_span_ids}),findings=findings))
    return ContextCoverage(input_sha256=digest(region),document_id=region.document_id,targets=records)


# 원래 판단은 보존하고 문맥 누락 때문에 지지할 수 없는 '필요 없음'만 새 보기에서 보류한다.
def guard_decisions(region,decisions,coverage):
    """Preserve raw decisions; only withhold unsupported absence in a new view."""
    if decisions is None:return None,[]
    by_id={r.target_cell_id:r for r in coverage.targets}
    stale=coverage.input_sha256!=digest(region) or coverage.document_id!=region.document_id
    updated=[];changes=[]
    for d in decisions.decisions:
        audit=by_id.get(d.target_cell_id)
        reasons=['AUDIT_INPUT_MISMATCH'] if stale else ['TARGET_AUDIT_UNAVAILABLE'] if audit is None else [f.kind+':'+f.reason for f in audit.findings if f.status in {'missing','unknown'}]
        if d.status=='not_needed' and (stale or audit is None or audit.status!='checked_no_gap'):
            missing='원본 대비 입력 문맥이 누락되었거나 확인되지 않음: '+'; '.join(reasons)
            updated.append(d.model_copy(update=dict(status='insufficient_evidence',relations=[],
                reason='입력 문맥 검사로 불필요 확정을 보류함. 원래 모델 판단은 raw 결과에 보존.',missing_evidence=missing)))
            changes.append(dict(target_cell_id=d.target_cell_id,before=d.status,after='insufficient_evidence',
                reasons=reasons,missing_span_ids=audit.missing_span_ids if audit else []))
        else:updated.append(d)
    return PeriodDecisions(decisions=updated),changes


# 문맥 검사·원본 판단·보류 적용 결과·비교와 별도 산출물을 전달하는 실행 상태다.
class CoverageState(TypedDict,total=False):
    coverage:dict
    raw:dict
    guarded_proposal:dict|None
    guarded_inventory:dict|None
    changes:list
    comparison:dict
    sidecar:dict


# 문맥 검사→기간 판단→근거 없는 부재 판단 보류를 연결한다.
def build_coverage_graph(region,target_ids,image_bytes,adapter,document,trace=None):
    from langgraph.graph import StateGraph,START,END
    from doc_understanding.runtime.telemetry import Trace
    trace=trace or Trace()
    # 현재 요청의 문맥 피복을 검사하고 대상별 누락 근거를 추적 기록에 남긴다.
    def audit(state):
        result=audit_context(region,target_ids,document)
        with trace.span('audit_source_context') as box:
            box['output']=dict(validation=[dict(cell_id=t.target_cell_id,status=t.status,
                items=[dict(kind=f.kind,status=f.status,source_cell_ids=f.source_cell_ids,span_refs=f.missing_span_ids,reason=f.reason) for f in t.findings]) for t in result.targets])
        return dict(coverage=result.model_dump(mode='json'))
    # 동일 입력으로 기간 판단 그래프를 실행해 원래 제안과 독립 검토를 얻는다.
    def resolve(state):
        return dict(raw=build_decision_graph(region,target_ids,image_bytes,adapter,trace).invoke({},config={'recursion_limit':10}))
    # 문맥 검사로 두 판단의 부재 주장을 제한하고 새 비교 결과와 별도 산출물을 만든다.
    def guard(state):
        coverage=ContextCoverage.model_validate(state['coverage']);raw=state['raw'];views={};changes=[]
        for key in ['proposal','inventory']:
            original=PeriodDecisions.model_validate(raw[key]) if raw[key] else None
            effective,delta=guard_decisions(region,original,coverage)
            views[key]=effective;changes.extend(dict(stage=key,**d) for d in delta)
        result=compare_decisions(region,target_ids,views['proposal'],views['inventory'])
        errors=sorted(set(raw['proposal_errors']+validate_decisions(region,target_ids,views['proposal'])))
        # Coverage must not launder a malformed raw response into a valid result.
        result['errors']=sorted(set(result['errors']+raw['comparison']['errors']))
        result['valid']=not result['errors']
        sidecar=dict(raw['sidecar'],schema_version=VERSION,
            status='rejected' if errors else 'review_required' if any(t.status!='checked_no_gap' for t in coverage.targets) else 'candidate',
            decisions=views['proposal'].model_dump(mode='json')['decisions'] if views['proposal'] else [],
            context_coverage=state['coverage'],context_changes=changes,validation_errors=errors)
        with trace.span('guard_absence_with_context') as box:
            box['output']=dict(semantic_status=sidecar['status'],validation=[dict(cell_id=d['target_cell_id'],phase=d['stage'],
                status=d['after'],reasons=d['reasons'],span_refs=d['missing_span_ids']) for d in changes],facts_published=0)
        return dict(guarded_proposal=views['proposal'].model_dump(mode='json') if views['proposal'] else None,
            guarded_inventory=views['inventory'].model_dump(mode='json') if views['inventory'] else None,
            changes=changes,comparison=result,sidecar=sidecar)
    graph=StateGraph(CoverageState)
    for name,fn in [('audit_source_context',audit),('resolve_period_decisions',resolve),('guard_absence_with_context',guard)]:graph.add_node(name,fn)
    graph.add_edge(START,'audit_source_context');graph.add_edge('audit_source_context','resolve_period_decisions')
    graph.add_edge('resolve_period_decisions','guard_absence_with_context');graph.add_edge('guard_absence_with_context',END)
    return graph.compile()
