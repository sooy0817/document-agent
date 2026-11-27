# 역할: 누락된 기간 문맥을 같은 추출의 원본 셀로 보충해 한 번 재검토한다. 축 부모나 원문 값을 수정하지 않는다.
# 역할: 기간 문맥이 부족할 때 원문 범위를 제한적으로 확장하며 원본 변경이나 의미 승인은 하지 않는다.
"""One bounded source-context expansion. No IR writes or semantic approval."""
import json
from typing import TypedDict

from doc_understanding.domain.axis_regions import AxisRegion, area, digest, encloses
from doc_understanding.parser.context.period_context import period_request
from doc_understanding.parser.context.period_decisions import DecisionAdapter
from doc_understanding.parser.context.period_coverage import audit_context, build_coverage_graph

VERSION = 'period-expansion/1.0'


# 감사에서 누락된 원본을 덮는 사용 가능한 물리 셀을 같은 추출에서 복사해 입력을 확장한다.
# 셀 수·바이트 예산 초과 시 적용하지 않고, 전후 해시·페이지·추가 시도와 미해결 사유를 기록한다.
def expand_context(region, targets, document, source_regions, *, max_cells=180,
                   max_bytes=100000):
    """Copy existing usable Physical cells that cover an audited Native source.

    A source region must come from the same immutable extraction. Page-local
    coordinates remain unchanged; page identity is provided separately. Native
    detection alone cannot authorize manufacturing a usable Physical cell.
    """
    before = audit_context(region, targets, document)
    native = {c.cell_id:(p.page_number,c) for p in document.pages for t in p.tables for c in t.cells}
    source = {}
    for r in source_regions:
        if r.document_id != region.document_id:
            raise ValueError('SOURCE_DOCUMENT_MISMATCH')
        for c in r.cells:
            if c.id in source and source[c.id] != (r.page,c):
                raise ValueError('CONFLICTING_SOURCE_CELL')
            source[c.id] = (r.page,c)
    pages = {c.id:region.page for c in region.cells}
    for c in region.cells:
        if c.id in source and source[c.id] != (region.page,c):
            raise ValueError('ORIGINAL_SOURCE_CELL_MISMATCH')
    selected = {c.id:c for c in region.cells}
    additions = []
    unresolved = []
    for target in before.targets:
        for finding in target.findings:
            if finding.status != 'missing':
                continue
            for native_id in finding.source_cell_ids:
                number, owner = native[native_id]
                missing = set(owner.span_refs) & set(finding.missing_span_ids)
                if not missing:
                    continue
                candidates = [c for page,c in source.values() if page == number and c.physical_usable
                    and set(c.spans) and set(c.spans) <= set(owner.span_refs)
                    and (encloses(owner.source.bbox,c.bbox) or
                         (set(c.spans)==set(owner.span_refs) and encloses(c.bbox,owner.source.bbox)))]
                exact = [c for c in candidates if set(c.spans)==set(owner.span_refs)]
                if len(exact)>1:
                    unresolved.append(dict(native_cell_id=native_id,reason='AMBIGUOUS_PHYSICAL_IDENTITY'))
                    continue
                candidates = exact or sorted(candidates,key=lambda c:(area(c.bbox),c.id))
                for c in candidates:
                    covered = {s for x in selected.values() for s in x.spans}
                    if not (missing - covered) & set(c.spans):
                        continue
                    selected[c.id] = c
                    pages[c.id] = number
                    additions.append(dict(cell_id=c.id,page=number,native_cell_id=native_id,
                        kind=finding.kind,trigger_target_id=target.target_cell_id,
                        source_span_ids=list(c.spans),relationship_status='source_context_candidate'))
    expanded = region.model_copy(update={'cells':tuple(selected.values())})
    # Do not silently truncate source text or select a partial context to fit budget.
    _, request = expansion_request(expanded,targets,pages,set(pages)-{c.id for c in region.cells})
    over_budget = len(expanded.cells)>max_cells or len(json.dumps(request,ensure_ascii=False).encode())>max_bytes
    if over_budget:
        expanded = region
        pages = {c.id:region.page for c in region.cells}
        unresolved.append(dict(reason='CONTEXT_BUDGET_EXCEEDED'))
    after = audit_context(expanded,targets,document)
    return dict(version=VERSION,original_sha256=digest(region),expanded_sha256=digest(expanded),
        original_region=region.model_dump(mode='json'),expanded_region=expanded.model_dump(mode='json'),
        before=before.model_dump(mode='json'),after=after.model_dump(mode='json'),
        cell_pages=pages,additions=[] if over_budget else additions,
        attempted_additions=additions,unresolved=unresolved,
        status='budget_blocked' if over_budget else 'expanded' if additions else 'unchanged',
        semantic_verified=False)


# 추가 원본 셀의 페이지와 입력 역할을 표시한다. 추가했다는 이유만으로 부모·계속표 관계를 확정하지 않는다.
def expansion_request(region,targets,pages,added_ids):
    aliases, data = period_request(region,targets)
    data['source']['shared_native_container'] = False
    for cell in data['source']['cells']:
        cell_id = aliases[cell['id']]
        cell['page_number'] = pages[cell_id]
        cell['input_role'] = 'additional_source_context' if cell_id in added_ids else 'original_region'
    data['source']['context_contract'] = (
        'Coordinates are page-local. Added cells are original source context, not confirmed '
        'parents or continuation links. Their inclusion does not establish applicability. '
        'The image panels are labelled by original page number. No previous model output is supplied.')
    return aliases,data


# 동일 모델·프롬프트를 쓰면서 입력 원본 묶음과 이미지만 확장하는 판단 어댑터다.
class ExpansionAdapter(DecisionAdapter):
    """Same prompts/model; only source packet and source image change."""
    # 확장 묶음을 보관하고 기존 외부 호출·예산 정책을 초기화한다.
    def __init__(self,packet,**kwargs):
        super().__init__(**kwargs)
        self.packet = packet

    # 현재 입력 해시가 확장 묶음과 같은지 검사한 뒤 페이지 정보를 붙인 요청을 만든다.
    def request_builder(self,region,targets):
        if digest(region)!=self.packet['expanded_sha256']:
            raise ValueError('EXPANSION_INPUT_MISMATCH')
        return expansion_request(region,targets,self.packet['cell_pages'],
                                 {a['cell_id'] for a in self.packet['additions']})


# 확장 묶음·재검토 결과·재시도 횟수·상태를 전달한다.
class ExpansionState(TypedDict,total=False):
    packet: dict
    result: dict
    retry_count: int
    status: str


# 누락 근거 수집 뒤 실제 확장됐을 때만 새 제안·독립 검토·피복 검사를 한 번 실행한다.
def build_expansion_graph(region,targets,document,source_regions,adapter_factory,image_factory,
                          trace=None):
    """Single acyclic retry: fresh generator and blind Judge, then coverage guard."""
    from langgraph.graph import StateGraph,START,END
    from doc_understanding.runtime.telemetry import Trace
    trace = trace or Trace()

    # 문맥 확장 후보를 만들고 추가 셀과 남은 피복 문제를 기록한다.
    def collect(state):
        packet = expand_context(region,targets,document,source_regions)
        with trace.span('collect_missing_source_context') as box:
            box['output'] = dict(status=packet['status'],source_cell_ids=[a['cell_id'] for a in packet['additions']],
                validation=[dict(cell_id=t['target_cell_id'],status=t['status'],span_refs=t['missing_span_ids'])
                            for t in packet['after']['targets']],facts_published=0)
        return dict(packet=packet,retry_count=0,status=packet['status'])

    # 확장된 입력과 이미지를 새 어댑터에 전달해 재검토하고 재시도 횟수를 1로 기록한다.
    def rejudge(state):
        packet = state['packet']; expanded = AxisRegion.model_validate(packet['expanded_region'])
        image = image_factory(packet)
        adapter = adapter_factory(packet)
        # This graph has no edge back to expansion and cannot recursively repair itself.
        result = build_coverage_graph(expanded,targets,image,adapter,document,trace).invoke({})
        return dict(result=result,retry_count=1,status='rejudged')

    graph = StateGraph(ExpansionState)
    graph.add_node('collect_missing_source_context',collect)
    graph.add_node('rejudge_once',rejudge)
    graph.add_edge(START,'collect_missing_source_context')
    graph.add_conditional_edges('collect_missing_source_context',
        lambda s:'rejudge_once' if s['packet']['status']=='expanded' else END)
    graph.add_edge('rejudge_once',END)
    return graph.compile()
