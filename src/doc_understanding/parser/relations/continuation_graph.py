# 역할: 표 조각 사이의 연속 관계를 근거 수집·후보 생성·검증 순서로 검토하는 선택적 흐름이다.
"""Source-grounded table continuation proposals, isolated from original IR.

The graph never approves semantic facts. Rules are a separate comparator, not a
filter on what the model may inspect. Audit lists use append reducers; a node
returns only its new events, never the accumulated list.
"""
from operator import add
from typing import Annotated, Literal
from typing_extensions import TypedDict

from doc_understanding.domain.models import EvidenceModel
from doc_understanding.parser.relations.logical_tables import aligned, contains, norm, usable

VERSION = 'continuation-graph/1.2'


# 모델이 인용한 원본 ID·문구·사용 이유를 담는다.
class SourceEvidence(EvidenceModel):
    source_id: str
    quote: str
    reason: str


# 어떤 원본 셀 관계가 어느 대상 셀까지 적용되는지 근거와 함께 제한한다.
class RelationScope(EvidenceModel):
    kind: Literal['header_inheritance', 'row_group_continuation']
    source_cell_ids: list[str]
    target_cell_ids: list[str]
    evidence: list[SourceEvidence]
    reason: str


# 계속표 관계의 판정·지지/반대 근거·부족한 근거·적용 범위를 담는 후보 모델이다.
class RelationDecision(EvidenceModel):
    decision: Literal['link_candidate', 'separate', 'insufficient_evidence']
    evidence_sufficient: bool
    supporting_evidence: list[SourceEvidence]
    contradicting_evidence: list[SourceEvidence]
    missing_evidence: list[str]
    scopes: list[RelationScope]
    reason: str


# 제안에 대한 별도 모델 검토 결과와 적용 범위 충족 여부를 담는다.
class RelationReview(EvidenceModel):
    verdict: Literal['supported', 'rejected', 'insufficient_evidence']
    scope_complete: bool
    evidence: list[SourceEvidence]
    reason: str


# 근거 묶음·규칙 기준·제안·검증·검토·오류·호출 횟수를 그래프 단계 사이에 전달한다.
class ContinuationState(TypedDict, total=False):
    pair: dict
    evidence: dict
    rule_baseline: dict
    proposal: dict
    validation: dict
    review: dict
    final: dict
    expansion_count: int
    model_calls: int
    steps: Annotated[list[dict], add]
    model_records: Annotated[list[dict], add]
    errors: Annotated[list[str], add]


# 관찰된 물리 표에서 같은 페이지의 연속 조각과 인접 페이지 가장자리 표 쌍을 검토 대상으로 만든다.
def discover_pairs(document):
    """Include adjacent page edge tables even with different columns/headings.

    Same-page vertically successive fragments are also considered. The inventory
    is observable; undetected physical tables cannot appear in this inventory.
    """
    physical = document.physical_tables
    top = [t for t in physical if not any(o.table_id != t.table_id
           and o.page_number == t.page_number and contains(o.bounding_box, t.bounding_box)
           for o in physical)]
    pages = {}
    for t in sorted(top, key=lambda t: (t.page_number, t.bounding_box.y0, t.bounding_box.x0)):
        pages.setdefault(t.page_number, []).append(t)
    pairs = []
    # 표 쌍의 ID·페이지·열 정렬 여부를 검토 목록에 기록한다. 연결을 확정하는 단계는 아니다.
    def append(a, b, kind):
        pairs.append(dict(id=a.table_id+'/to/'+b.table_id, source_table_id=a.table_id,
                          target_table_id=b.table_id, kind=kind,
                          source_page=a.page_number, target_page=b.page_number,
                          aligned_columns=aligned(a, b)))
    for number, tables in pages.items():
        for a, b in zip(tables, tables[1:]):
            if a.bounding_box.y1 <= b.bounding_box.y0:
                append(a, b, 'same_page_fragments')
        if number+1 in pages:
            # Keep tied bottom/top regions rather than silently guessing a column.
            end = max(t.bounding_box.y1 for t in tables)
            start = min(t.bounding_box.y0 for t in pages[number+1])
            for a in tables:
                for b in pages[number+1]:
                    if a.bounding_box.y1 >= end-1.5 and b.bounding_box.y0 <= start+1.5:
                        append(a, b, 'adjacent_pages')
    return pairs


# 표 쌍 주변의 원본 근거와 참조 목록을 모으고 입력 크기·피복 문제를 기록한다.
def collect_packet(document, pair, expanded=False, max_bytes=220000):
    tables = {t.table_id:t for t in document.physical_tables}
    a, b = tables[pair['source_table_id']], tables[pair['target_table_id']]
    numbers = {a.page_number, b.page_number}
    if expanded:
        numbers.update((min(numbers)-1, max(numbers)+1))
    available = {p.page_number:p for p in document.document_ir.pages}
    numbers &= set(available)
    registry = {}
    for t in document.physical_tables:
        if t.page_number not in numbers:
            continue
        for c in t.cells:
            registry[c.cell_id] = dict(id=c.cell_id, kind='cell', page=t.page_number,
                table_id=t.table_id, text=c.original_text or '', bbox=list(c.source_bbox.xyxy),
                row=c.row_index, col=c.column_index, rowspan=c.rowspan, colspan=c.colspan,
                physical_usable=usable(c), span_refs=list(c.span_refs))
    # Original spans also cover titles/notes outside the selected tables.
    for n in sorted(numbers):
        for block in available[n].blocks:
            for s in block.spans:
                registry[s.span_id] = dict(id=s.span_id, kind='span', page=n,
                    text=s.text_raw, bbox=list(s.source.bbox.xyxy))
    boundary_ids=[]
    for cid,c in registry.items():
        if c['kind']!='span' or not norm(c['text']):
            continue
        import re
        if re.fullmatch(r'[-–—\d]+',norm(c['text'])):
            continue
        y=(c['bbox'][1]+c['bbox'][3])/2
        between=(a.bounding_box.y1 < y < b.bounding_box.y0 if a.page_number==b.page_number
                 else (c['page']==a.page_number and y>a.bounding_box.y1)
                 or (c['page']==b.page_number and y<b.bounding_box.y0))
        if c['page'] in (a.page_number,b.page_number) and between:
            boundary_ids.append(cid)
    packet = dict(pair=pair, pages=sorted(numbers), registry=registry,
        separation_boundary_ids=boundary_ids,
        expanded=expanded, coverage_issues=[], semantic_verified=False)
    for t in (a, b):
        if not t.cells or any(not usable(c) for c in t.cells):
            packet['coverage_issues'].append('UNRESOLVED_PHYSICAL_CELLS:'+t.table_id)
    import json
    if len(json.dumps(packet, ensure_ascii=False).encode()) > max_bytes:
        packet['coverage_issues'].append('SOURCE_PACKET_BUDGET_EXCEEDED')
    return packet


# 인용한 ID·문구가 원본 목록과 일치하고 사용 이유가 있는지 검사한다.
def evidence_errors(evidence, registry):
    errors = []
    for e in evidence:
        source = registry.get(e.source_id)
        if not source:
            errors.append('UNKNOWN_EVIDENCE_ID')
        elif (not norm(e.quote) and not (source['kind']=='cell' and source.get('physical_usable')
                                       and not norm(source['text']))) or norm(e.quote) not in norm(source['text']):
            errors.append('QUOTE_NOT_IN_SOURCE')
        if not e.reason.strip():
            errors.append('EVIDENCE_REASON_MISSING')
    return errors


# 제안의 원본 인용·관계 범위·자기 참조 등 무결성을 검사한다. 의미 정확도를 증명하지 않는다.
def validate_decision(packet, proposed):
    """Source integrity checks, explicitly NOT a semantic correctness proof."""
    d = RelationDecision.model_validate(proposed)
    r, p = packet['registry'], packet['pair']
    errors = evidence_errors(d.supporting_evidence+d.contradicting_evidence, r)
    if not d.reason.strip():
        errors.append('DECISION_REASON_MISSING')
    if d.decision != 'insufficient_evidence':
        if not d.evidence_sufficient or d.missing_evidence or packet['coverage_issues']:
            errors.append('INSUFFICIENT_SOURCE_CANNOT_BE_DECIDED')
        evidence = d.supporting_evidence+d.contradicting_evidence
        if not evidence:
            errors.append('DECISION_EVIDENCE_MISSING')
        if d.decision == 'separate' and not {e.source_id for e in evidence} & set(packet.get('separation_boundary_ids', [])):
            # Different body row topics do not establish a new table boundary.
            # Missing boundary evidence triggers context expansion, not acceptance.
            errors.append('SEPARATION_BOUNDARY_NOT_CITED')
        if d.decision == 'link_candidate' and not {p['source_page'], p['target_page']} <= {
                r[e.source_id]['page'] for e in evidence if e.source_id in r}:
            errors.append('LINK_REQUIRES_BOTH_PAGE_EVIDENCE')
    if d.decision != 'link_candidate' and d.scopes:
        errors.append('NONLINK_WITH_INHERITANCE')
    used = {}
    for scope in d.scopes:
        errors += evidence_errors(scope.evidence, r)
        # A set of sibling headers crossed with all target cells is ambiguous.
        # Multi-level ancestors use separate atomic edges to the same target.
        if len(scope.source_cell_ids) != 1:
            errors.append('SCOPE_MUST_HAVE_ONE_SOURCE_HEADER')
        if not scope.source_cell_ids or not scope.target_cell_ids or not scope.evidence or not scope.reason.strip():
            errors.append('SCOPE_EVIDENCE_OR_MEMBERS_MISSING')
        if len(set(scope.source_cell_ids)) != len(scope.source_cell_ids) or len(set(scope.target_cell_ids)) != len(scope.target_cell_ids):
            errors.append('DUPLICATE_SCOPE_MEMBER')
        cited = {e.source_id for e in scope.evidence}
        if not set(scope.source_cell_ids) <= cited:
            errors.append('SCOPE_SOURCE_NOT_CITED')
        for cid in scope.source_cell_ids:
            c = r.get(cid, {})
            if c.get('kind') != 'cell' or not c.get('physical_usable') or not norm(c.get('text')):
                errors.append('INVALID_SOURCE_HEADER')
            elif c['page'] > p['source_page'] or c['table_id'] == p['target_table_id']:
                errors.append('HEADER_OUTSIDE_PREDECESSOR_SCOPE')
        for cid in scope.target_cell_ids:
            c = r.get(cid, {})
            if c.get('table_id') != p['target_table_id'] or not c.get('physical_usable'):
                errors.append('INVALID_TARGET_CELL')
            key = (scope.kind, cid)
            for prior in used.get(key, []):
                if prior == scope.source_cell_ids:
                    errors.append('DUPLICATE_SCOPE_EDGE')
                if scope.kind == 'header_inheritance' and len(prior)==len(scope.source_cell_ids)==1:
                    left, right = r.get(prior[0],{}), r.get(scope.source_cell_ids[0],{})
                    if left.get('table_id') == right.get('table_id') and 'bbox' in left and 'bbox' in right:
                        a,b=left['bbox'],right['bbox']
                        if not ((a[0]<=b[0]+1.5 and a[2]>=b[2]-1.5) or
                                (b[0]<=a[0]+1.5 and b[2]>=a[2]-1.5)):
                            errors.append('SIBLING_HEADERS_FOR_SAME_TARGET')
            used.setdefault(key, []).append(scope.source_cell_ids)
            if cid in scope.source_cell_ids:
                errors.append('SELF_RELATION')
    return dict(valid=not errors, errors=sorted(set(errors)), semantic_verified=False)


# 근거 수집→판정→검증→검토를 연결한다. 문맥 확장은 한 번만 허용하며 원본 IR은 바꾸지 않는다.
def build_continuation_graph(document, pair, *, image_loader, model=None, trace=None):
    """At most two judgments (one context expansion) and one semantic review.

    Model provider is injectable for mocks/replay. Source values stay in an
    invocation-local packet; only compact node summaries go to telemetry.
    """
    from contextlib import nullcontext
    from langgraph.graph import StateGraph, START, END
    from doc_understanding.parser.relations.logical_tables import build_logical_links

    # 그래프 단계의 간단한 진행 기록을 만든다.
    def event(name, **data):
        return [dict(node=name, **data)]

    # 원본 묶음과 규칙 기반 연결 기준을 수집하고 실행 상태를 초기화한다.
    def collect_evidence(state):
        packet = collect_packet(document, pair)
        baseline = next((l.model_dump(mode='json') for l in
            build_logical_links(document.document_ir, document.physical_tables)
            if l.source_table_id == pair['source_table_id'] and l.target_table_id == pair['target_table_id']), None)
        return dict(pair=pair, evidence=packet, expansion_count=0, model_calls=0,
            rule_baseline=dict(linked=baseline is not None, link=baseline),
            steps=event('collect_evidence', pages=packet['pages'], sources=len(packet['registry'])))

    # 수집한 근거의 피복 문제를 단계 기록에 남긴다.
    def assess_coverage(state):
        return dict(steps=event('assess_coverage', issues=state['evidence']['coverage_issues']))

    # 모델 판정을 호출하고 실패하면 근거 부족 결과와 오류 기록으로 전환한다.
    def judge_relation(state):
        try:
            if model is None:
                raise ValueError('MODEL_DISABLED')
            proposal, record = model.judge(state['evidence'], image_loader(state['evidence']), None)
            parsed = RelationDecision.model_validate(proposal)
            return dict(proposal=parsed.model_dump(mode='json'), model_calls=state['model_calls']+1,
                model_records=[record], steps=event('judge_relation', decision=parsed.decision))
        except Exception as exc:
            record = getattr(exc, 'record', dict(status='failed', error_type=type(exc).__name__))
            return dict(proposal=RelationDecision(decision='insufficient_evidence', evidence_sufficient=False,
                supporting_evidence=[], contradicting_evidence=[], missing_evidence=['MODEL_OR_INPUT_UNAVAILABLE'],
                scopes=[], reason='모델 실행 또는 응답 검증 실패').model_dump(mode='json'),
                model_records=[record], model_calls=state['model_calls']+int(record.get('attempts', 0)),
                errors=['MODEL_FAILED:'+type(exc).__name__], steps=event('judge_relation', status='failed'))

    # 주변 원본 문맥을 넓혀 한 번의 근거 확장 결과를 만든다.
    def expand_evidence(state):
        packet = collect_packet(document, pair, expanded=True)
        return dict(evidence=packet, expansion_count=1,
                    steps=event('expand_evidence', pages=packet['pages']))

    # 현재 제안이 수집된 원본과 적용 범위를 지키는지 검사한다.
    def validate_relation(state):
        validation = validate_decision(state['evidence'], state['proposal'])
        return dict(validation=validation, steps=event('validate_relation', **validation))

    # 제안에 대한 별도 모델 검토를 실행하고 인용 근거를 다시 검사한다.
    def review_relation(state):
        try:
            review, record = model.judge(state['evidence'], image_loader(state['evidence']), state['proposal'])
            review = RelationReview.model_validate(review)
            errors = evidence_errors(review.evidence, state['evidence']['registry'])
            if not review.evidence or not review.reason.strip():
                errors.append('REVIEW_EVIDENCE_MISSING')
            return dict(review=review.model_dump(mode='json'), errors=errors, model_records=[record],
                model_calls=state['model_calls']+1, steps=event('review_relation', verdict=review.verdict))
        except Exception as exc:
            record = getattr(exc, 'record', dict(status='failed', error_type=type(exc).__name__))
            return dict(errors=['REVIEW_FAILED:'+type(exc).__name__], model_records=[record],
                model_calls=state['model_calls']+int(record.get('attempts', 0)),
                steps=event('review_relation', status='failed'))

    # 무결성 검사와 모델 검토가 지지하는 범위만 최종 후보에 남긴다. semantic_verified는 계속 False다.
    def finalize(state):
        d = state.get('proposal', {})
        review = state.get('review', {})
        accepted = (state.get('validation', {}).get('valid') and not state.get('errors')
                    and review.get('verdict') == 'supported' and review.get('scope_complete'))
        decision = d.get('decision', 'insufficient_evidence') if accepted else 'insufficient_evidence'
        reasons = list(state.get('errors', []))+state['evidence']['coverage_issues']
        reasons += state.get('validation', {}).get('errors', [])
        reasons += d.get('missing_evidence', [])
        if not accepted:
            reasons.append('SEMANTIC_REVIEW_NOT_SUPPORTED')
        return dict(final=dict(version=VERSION, decision=decision, scopes=d.get('scopes', []) if accepted else [],
                    reasons=sorted(set(reasons)), semantic_verified=False, facts_published=0),
                    steps=event('finalize', decision=decision))

    # 단계 함수를 실행 추적으로 감싸 원문 전체 대신 요약을 기록하게 한다.
    def wrapped(name, fn):
        # 단계를 실행하고 판정·오류·호출 사용량 등의 요약을 추적 결과에 넣는다.
        def run(state):
            context = trace.span(name, dict(source_cell_ids=[pair['source_table_id'],pair['target_table_id']]),
                generation=name in ('judge_relation','review_relation')) if trace else nullcontext({})
            with context as box:
                result = fn(state)
                box['output'] = dict(result=result.get('final', result.get('validation')),
                    reasons=result.get('errors', []), status=result.get('proposal', {}).get('decision'),
                    source_cell_ids=[e['source_id'] for e in result.get('proposal', {}).get('supporting_evidence', [])])
                if result.get('model_records'):
                    record=result['model_records'][-1]
                    box['output'].update(usage=record.get('usage') or {},
                        estimated_cost_usd=record.get('estimated_cost_usd'),model=record.get('model'),
                        reason=result.get('review',result.get('proposal',{})).get('reason'))
                return result
        return run

    builder = StateGraph(ContinuationState)
    nodes = [collect_evidence, assess_coverage, judge_relation, expand_evidence,
             validate_relation, review_relation, finalize]
    for node in nodes:
        builder.add_node(node.__name__, wrapped(node.__name__, node))
    builder.add_edge(START, 'collect_evidence')
    builder.add_edge('collect_evidence', 'assess_coverage')
    builder.add_conditional_edges('assess_coverage',
        lambda s: 'finalize' if s['evidence']['coverage_issues'] else 'judge_relation')
    # 근거 부족이고 확장 전일 때만 문맥 확장으로 분기한다.
    def after_judgment(s):
        if (not s.get('errors') and s['proposal']['decision'] == 'insufficient_evidence'
                and s['expansion_count'] == 0):
            return 'expand_evidence'
        return 'validate_relation'
    builder.add_conditional_edges('judge_relation', after_judgment)
    builder.add_edge('expand_evidence', 'assess_coverage')
    # 분리 근거 누락 시 한 번 확장하고, 유효한 제안만 추가 검토로 보낸다.
    def after_validation(s):
        if ('SEPARATION_BOUNDARY_NOT_CITED' in s['validation']['errors']
                and s['expansion_count']==0 and not s.get('errors')):
            return 'expand_evidence'
        return ('review_relation' if s['validation']['valid']
                and s['proposal']['decision']!='insufficient_evidence' and not s.get('errors') else 'finalize')
    builder.add_conditional_edges('validate_relation', after_validation)
    builder.add_edge('review_relation', 'finalize')
    builder.add_edge('finalize', END)
    return builder.compile()
