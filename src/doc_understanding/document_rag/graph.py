"""Identity-mode LangGraph over the existing RAG steps (default, phase 1–3)."""
from typing import TypedDict
from . import repository, service


class RagState(TypedDict, total=False):
    original_question: str
    search_request: dict
    candidates: list
    evidence: list
    validation: dict
    result: dict
    errors: list
    understanding: dict
    retry_count: int
    retry_history: list


def build_graph(output, *, agent=False, interpreter=None, retriever=None, resolver=None,
                validator=None, answer_generator=None, **generation_options):
    from langgraph.graph import StateGraph, START, END

    def understand_question(state):
        request = state['search_request'].copy()
        plan = dict(original_query=state['original_question'], rewritten_query=state['original_question'],
                    mode='identity', provenance='passthrough', verified_conditions={})
        if agent:
            from .understanding import understand
            plan = understand(state['original_question'], interpreter=interpreter)
            request['query'] = plan['rewritten_query']
        return dict(understanding=plan, search_request=request)

    def retrieve(state):
        r = state['search_request']
        return dict(candidates=(retriever or repository.search)(r['query'], r.get('run_id'), r.get('limit', 8)))

    def resolve_evidence(state):
        return dict(evidence=(resolver or service.resolve_evidence)(state['candidates'], state['original_question']))

    def validate_evidence(state):
        validation = (validator or service.validate_evidence)(state['evidence'], state['original_question'])
        if agent:
            # Original explicit entity remains authoritative even if an LLM guessed another one.
            import re
            entities = re.findall(r'[가-힣A-Za-z0-9]+(?:농협|축협)', state['original_question'])
            if entities and not any(any(entity in e.get('source_name', '') for entity in entities)
                                    for e in state['evidence']):
                validation['sufficient'] = False
                validation['reasons'].append('ORIGINAL_ENTITY_NOT_IN_EVIDENCE')
            metrics = [m for m in state['understanding'].get('metrics', []) if m in state['original_question']]
            if state['understanding'].get('question_type') == 'cell_lookup' and metrics:
                paths = [' '.join(e.get('row_path', []) + e.get('column_path', [])).replace('\n', '')
                         for e in state['evidence'] if e.get('table_id') and e.get('lookup_eligible')]
                if not any(all(all(t in path for t in metric.split()) for metric in metrics) for path in paths):
                    validation['sufficient'] = False
                    validation['reasons'].append('REQUESTED_METRIC_NOT_IN_PATHS')
            plan = {**state['understanding'], 'verified_conditions': {
                'periods': validation['periods'] if 'REQUESTED_PERIOD_NOT_IN_COLUMNS' not in validation['reasons'] else [],
                'provenance': 'observed_column_labels_only_not_semantic_approval'}}
            return dict(validation=validation, understanding=plan)
        return dict(validation=validation)

    def generate_answer(state):
        if agent and not state['validation']['sufficient']:
            from .answer import render_answer
            selection = dict(evidence_ids=[], values_raw=[], abstained=True)
            return dict(result=dict(question=state['original_question'], selection=selection,
                answer=render_answer(selection, state['evidence']), model_calls=0,
                generation_mode='validation_abstention', facts_published=0,
                semantic_status='unvalidated_candidate', reasons=state['validation']['reasons']))
        return dict(result=(answer_generator or service.generate_answer)(state['original_question'],
            state['evidence'], output, **generation_options))

    def retry(state):
        reason = state['validation']['reasons']
        return dict(retry_count=1, search_request={**state['search_request'], 'query': state['original_question']},
                    retry_history=[dict(reason=reason, query=state['original_question'], attempt=1)])

    def guarded(name, function):
        def execute(state):
            try:
                return function(state)
            except Exception as error:
                # No raw exceptions/source dumps in callback events. No automatic model retries.
                return dict(errors=[*state.get('errors', []), {'node': name, 'error': type(error).__name__}])
        return execute

    g = StateGraph(RagState)
    for name, fn in [('understand_question', understand_question), ('retrieve', retrieve),
                     ('resolve_evidence', resolve_evidence), ('validate_evidence', validate_evidence),
                     ('generate_answer', generate_answer)]:
        g.add_node(name, guarded(name, fn))
    g.add_edge(START, 'understand_question')
    for source, target in [('understand_question', 'retrieve'), ('retrieve', 'resolve_evidence'),
                           ('resolve_evidence', 'validate_evidence')]:
        g.add_conditional_edges(source, lambda s, t=target: END if s.get('errors') else t,
                                {END: END, target: target})
    if agent:
        g.add_node('retry_original_question', guarded('retry_original_question', retry))
        g.add_conditional_edges('validate_evidence', lambda s: END if s.get('errors') else
            'retry_original_question' if not s['validation']['sufficient'] and s.get('retry_count', 0) < 1
            else 'generate_answer', {END: END, 'retry_original_question': 'retry_original_question',
                                     'generate_answer': 'generate_answer'})
        g.add_conditional_edges('retry_original_question', lambda s: END if s.get('errors') else 'retrieve',
                                {END: END, 'retrieve': 'retrieve'})
    else:
        g.add_conditional_edges('validate_evidence', lambda s: END if s.get('errors') else 'generate_answer',
                                {END: END, 'generate_answer': 'generate_answer'})
    g.add_edge('generate_answer', END)
    return g.compile()


def run(question, output, *, run_id=None, limit=8, trace=None, agent=False, interpreter=None,
        trace_mode=None, **kwargs):
    graph = build_graph(output, agent=agent, interpreter=interpreter, **kwargs)
    state = dict(original_question=question, search_request=dict(query=question, run_id=run_id, limit=limit),
                 candidates=[], evidence=[], errors=[], retry_count=0, retry_history=[])
    if trace:
        with trace.request(question, trace_mode or ('agent' if agent else 'identity')) as root:
            result = graph.invoke(state, config={'callbacks': trace.callbacks(), 'recursion_limit': 16})
            root['output'] = result
        result['trace_id'] = trace.trace_id
        return result
    return graph.invoke(state, config={'recursion_limit': 16})
