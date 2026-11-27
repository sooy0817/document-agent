# 역할: 행열 관계 후보 생성과 검증을 제한된 순서로 연결하며 원본 IR을 변경하지 않는다.
"""Bounded structure-understanding workflow; source IR and facts remain untouched.

The graph orchestrates existing proposals/validators. It does not certify semantics
or silently expand the single-level candidate generator. Source payloads are kept
in invocation-local closures; no external tracing callback is enabled by default.
"""
from typing import Callable, TypedDict

from doc_understanding.agents.axis_review.axis_resolver import AxisProposal, digest, geometry_options, rule_proposal
from doc_understanding.agents.axis_review.axis_validation import audit_mapping_coverage, audit_region, validated_views


class AxisState(TypedDict, total=False):
    region_id: str
    source_hash: str
    physical_cells: int
    physical_usable: int
    rule_proposal: dict
    proposal: dict
    rule_supported_ids: list[str]
    pending_option_ids: list[str]
    route_reason: str
    model_invocations: int
    model_record: dict
    model_proposal: dict
    errors: list[str]
    validation: dict
    baseline_coverage: list[dict]
    structured: list[dict]
    steps: list[dict]
    facts_published: int
    semantic_verified: bool


def build_axis_graph(region, baselines=(), *, resolver: Callable | None = None):
    """One graph per immutable region; resolver is injected for live/mock/replay.

    At most one resolver invocation, only if unhandled geometry options exist.
    The caller owns page/run API budget. A model error preserves rule candidates.
    """
    from langgraph.graph import END, START, StateGraph

    def step(state, name, **details):
        return state.get('steps', []) + [dict(node=name, **details)]

    def inspect_source(state):
        return dict(region_id=region.id, source_hash=digest(region),
                    physical_cells=len(region.cells),
                    physical_usable=sum(c.physical_usable for c in region.cells),
                    model_invocations=0, errors=[], facts_published=0, semantic_verified=False,
                    baseline_coverage=[c.model_dump(mode='json') for c in
                                       audit_mapping_coverage(region, baselines)],
                    steps=step(state, 'inspect_source'))

    def judge_rules(state):
        proposal = rule_proposal(region)
        audit = audit_region(region, proposal)
        supported = [c.proposal.cell_id for c in audit.checked_links
                     if c.status == 'supported_candidate']
        return dict(rule_proposal=proposal.model_dump(mode='json'),
                    proposal=proposal.model_dump(mode='json'), rule_supported_ids=supported,
                    steps=step(state, 'judge_rules', proposed=len(proposal.links),
                               supported_candidates=len(supported)))

    def assess_gap(state):
        pending = [p.cell_id for p in geometry_options(region)
                   if p.cell_id not in state['rule_supported_ids']]
        if not pending:
            reason = ('NO_UNHANDLED_GEOMETRY_OPTIONS' if state['rule_supported_ids']
                      else 'NO_GEOMETRY_OPTIONS')
        elif resolver is None:
            reason = 'MODEL_DISABLED'
        else:
            reason = 'UNRESOLVED_GEOMETRY_OPTIONS'
        return dict(pending_option_ids=pending, route_reason=reason,
                    steps=step(state, 'assess_gap', reason=reason, pending=len(pending)))

    def judge_llm(state):
        original = AxisProposal.model_validate(state['rule_proposal'])
        try:
            proposed, record = resolver(region)
            proposed = AxisProposal.model_validate(proposed)
            # Freeze supported rules. A model can add candidates, not override them.
            fixed = [p for p in original.links if p.cell_id in state['rule_supported_ids']]
            pending = set(state['pending_option_ids'])
            additions = [p for p in proposed.links if p.cell_id in pending]
            ignored = [p.cell_id for p in proposed.links if p.cell_id not in pending]
            # Preserve duplicates/unknown references for the validator to reject.
            combined = fixed + additions
            linked = {p.cell_id for p in combined}
            merged = AxisProposal(links=combined,
                abstain_cell_ids=[c.id for c in region.cells if c.id not in linked])
            check = audit_region(region, merged)
            supported = {c.proposal.cell_id for c in check.checked_links
                         if c.status == 'supported_candidate'}
            conflicts = set(state['rule_supported_ids']) - supported
            errors = []
            if conflicts:
                merged = original
                errors = ['MODEL_BATCH_CONFLICTS_WITH_SUPPORTED_RULES']
            return dict(proposal=merged.model_dump(mode='json'),
                        model_proposal=proposed.model_dump(mode='json'), model_record=record,
                        model_invocations=1, errors=errors,
                        steps=step(state, 'judge_llm', proposed=len(proposed.links),
                                   ignored_targets=ignored, rule_conflicts=sorted(conflicts)))
        except Exception as exc:
            # Exception messages may contain auth/request data; retain only type.
            return dict(model_invocations=1, errors=['MODEL_FAILED:'+type(exc).__name__],
                        model_record={'status': 'failed', 'error_type': type(exc).__name__},
                        steps=step(state, 'judge_llm', status='failed'))

    def validate_paths(state):
        audit = audit_region(region, AxisProposal.model_validate(state['proposal']))
        return dict(validation=audit.model_dump(mode='json'),
                    steps=step(state, 'validate_paths',
                               supported_candidates=sum(c.status == 'supported_candidate'
                                                        for c in audit.checked_links),
                               rejected=sum(c.status == 'unresolved' for c in audit.checked_links)))

    def materialize(state):
        from doc_understanding.agents.axis_review.axis_validation import AxisAudit
        audit = AxisAudit.model_validate(state['validation'])
        views = validated_views(region, audit, baselines)
        return dict(structured=[v.model_dump(mode='json') for v in views],
                    steps=step(state, 'materialize_candidate', tables=len(views)))

    graph = StateGraph(AxisState)
    for name, node in [('inspect_source', inspect_source), ('judge_rules', judge_rules),
                       ('assess_gap', assess_gap), ('judge_llm', judge_llm),
                       ('validate_paths', validate_paths), ('materialize_candidate', materialize)]:
        graph.add_node(name, node)
    graph.add_edge(START, 'inspect_source')
    graph.add_edge('inspect_source', 'judge_rules')
    graph.add_edge('judge_rules', 'assess_gap')
    graph.add_conditional_edges('assess_gap',
        lambda s: 'llm' if s['route_reason'] == 'UNRESOLVED_GEOMETRY_OPTIONS' else 'validate',
        {'llm': 'judge_llm', 'validate': 'validate_paths'})
    graph.add_edge('judge_llm', 'validate_paths')
    graph.add_edge('validate_paths', 'materialize_candidate')
    graph.add_edge('materialize_candidate', END)
    return graph.compile()
