"""teams / table_review / graph."""

import hashlib
import json
from pathlib import Path

from doc_understanding.agents.roles import relation_analyst, relation_judge, role_classifier
from doc_understanding.runtime import telemetry
from doc_understanding.runtime import artifacts
from doc_understanding.agents.workflows.table_review import state as table_review_state
from doc_understanding.agents.workflows.table_review import version
from doc_understanding.tools import focused_evidence, relation_candidates, semantic_candidates
from doc_understanding.validation import relations, semantic_gate


def build_graph(
    *, classify_roles, infer_relations, judge_relations, validate_relations_and_storage,
    classify_required=True
):
    """Compile the team without opening a provider, reading evidence, or starting a queue."""
    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(table_review_state.State)
    graph.add_node("classify_roles", classify_roles)
    graph.add_node("infer_relations", infer_relations)
    graph.add_node("judge_relations", judge_relations)
    graph.add_node("validate_relations_and_storage", validate_relations_and_storage)
    graph.add_edge(START, "classify_roles" if classify_required else "infer_relations")
    graph.add_edge("classify_roles", "infer_relations")
    graph.add_edge("infer_relations", "judge_relations")
    graph.add_edge("judge_relations", "validate_relations_and_storage")
    graph.add_edge("validate_relations_and_storage", END)
    return graph.compile()


def plan_review(record, data):
    snap = record['snapshot']
    folder = Path(data) / snap['job_id']
    if folder.resolve().parent != Path(data).resolve():
        raise ValueError('INVALID_JOB_PATH')
    for name, key in [('document.json', 'canonical_sha256'), ('input.pdf', 'source_pdf_sha256')]:
        if hashlib.sha256((folder / name).read_bytes()).hexdigest() != snap[key]:
            raise ValueError('SOURCE_CHANGED')
    doc = artifacts.read(folder / 'document.json')
    selection = snap['selection']
    table = next((t for t in doc['physical_tables'] if t['table_id'] == selection.get('table_id')), None)
    if table is None or table['page_number'] != selection['page']:
        raise ValueError('SELECTED_TABLE_NOT_FOUND')
    ids = [selection['cell_id']] if selection.get('cell_id') else [c['cell_id'] for c in table['cells']]
    if not ids or set(ids) - {c['cell_id'] for c in table['cells']}:
        raise ValueError('SELECTED_CELL_NOT_FOUND')
    sources = {c['cell_id']: c.get('original_text') or '' for t in doc['physical_tables'] for c in t['cells']}
    candidates = semantic_candidates.generate(doc, ids)
    claim = record.get('review_decision') or {}
    contested = set(claim.get('source_ids') or ids)
    if table['table_id'] in contested:
        contested.update(ids)
    routes = [semantic_gate.decide(c, sources, human_conflict=(
        claim.get('choice') in ('disagree', 'uncertain') and c.cell_id in contested)) for c in candidates]
    return sources, candidates, routes


def _rule_target(candidate, sources, *, blocked=False):
    def axis(ids):
        refs = tuple(ids) + (candidate.cell_id,)
        return dict(status='contextual_candidate' if ids else 'unknown', header_ids=list(ids),
                    reason='Source-bound rule candidate; not human approved.',
                    citations=[dict(source_id=s, quote=sources[s][:160]) for s in refs if sources.get(s)])
    return dict(cell_id=candidate.cell_id, role=candidate.role,
                row_axis=axis(() if blocked else candidate.row_ids),
                column_axis=axis(() if blocked else candidate.column_ids), context_ids=[])


def run_review(record, data, run_dir, *, calls, trace=None, progress=None):
    """Rule-first review. Every selected cell has an outcome, including blocked cells."""
    from langgraph.graph import END, START, StateGraph
    progress = progress or (lambda stage: None)
    trace = trace or telemetry.Trace()
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    def pin(name, value):
        dest = run_dir / name
        if dest.exists() and artifacts.digest(artifacts.read(dest)) != artifacts.digest(value):
            raise ValueError('PINNED_INPUT_CHANGED')
        if not dest.exists():
            artifacts.save(dest, value)

    sources, candidates, routes = plan_review(record, data)
    ids = [c.cell_id for c in candidates]
    by_id = {c.cell_id: c for c in candidates}
    routing = {r.cell_id: r for r in routes}
    pin('semantic-plan.json', dict(version=version.VERSION, request_digest=artifacts.digest(record),
                                  candidates=[c.model_dump(mode='json') for c in candidates],
                                  routes=[r.model_dump(mode='json') for r in routes]))
    saved = run_dir / 'result.json'
    if saved.exists():
        result = artifacts.read(saved)
        if result.get('version') != version.VERSION:
            raise ValueError('NEW_REVIEW_REQUEST_REQUIRED')
        return result
    records, batches, outcomes, storage, errors, warnings, missing = [], [], {}, [], [], [], []
    interpretations = {cid: _rule_target(by_id[cid], sources, blocked=routing[cid].route == 'blocked') for cid in ids}

    def prepare(state):
        progress('semantic_candidates')
        return dict(candidates=[c.model_dump(mode='json') for c in candidates])

    def route(state):
        progress('semantic_gate')
        for r in routes:
            if r.route != 'llm':
                outcomes[r.cell_id] = dict(cell_id=r.cell_id, status='rule_candidate' if r.route == 'rule' else 'blocked',
                                           route=r.route, reasons=list(r.reasons))
        return dict(routes=[r.model_dump(mode='json') for r in routes],
                    needs_model=any(r.route == 'llm' for r in routes))

    def prepare_batch(group):
        refs = tuple(dict.fromkeys(s for cid in group for s in by_id[cid].context_ids))
        try:
            packet, images, index, audit = focused_evidence.prepare(
                record, data, target_ids=group, context_source_ids=refs)
            if packet['omitted_ids'] or set(group + list(refs)) - {e['id'] for e in packet['evidence']}:
                raise ValueError('FOCUSED_CONTEXT_INCOMPLETE')
            enriched = relation_candidates.with_candidates(packet)
            alternatives = {(c['cell_id'], c['axis'], c['header_id']) for c in enriched['geometric_candidates']}
            for cid in group:
                for axis, refs_axis in [('row', by_id[cid].row_ids), ('column', by_id[cid].column_ids)]:
                    alternatives.update((cid, axis, ref) for ref in refs_axis)
            if len(alternatives) > 160:
                raise ValueError('RELATION_CANDIDATE_BUDGET_EXCEEDED')
            enriched['geometric_candidates'] = [dict(cell_id=c, axis=a, header_id=h) for c, a, h in sorted(alternatives)]
            enriched['rule_candidates'] = [dict(cell_id=cid, row_ids=by_id[cid].row_ids,
                column_ids=by_id[cid].column_ids, reasons=routing[cid].reasons,
                policy='Unverified alternatives, not answers.') for cid in group]
            if len(json.dumps(enriched, ensure_ascii=False).encode('utf-8')) > 100_000:
                raise ValueError('FOCUSED_INPUT_BUDGET_EXCEEDED')
            return [(group, packet, enriched, images, index, audit)]
        except ValueError as exc:
            if str(exc) not in ('FOCUSED_CONTEXT_INCOMPLETE', 'FOCUSED_INPUT_BUDGET_EXCEEDED',
                                'RELATION_CANDIDATE_BUDGET_EXCEEDED', 'REQUIRED_CONTEXT_MISSING'):
                raise
            if len(group) > 1:
                mid = len(group) // 2
                return prepare_batch(group[:mid]) + prepare_batch(group[mid:])
            cid = group[0]
            outcomes[cid] = dict(cell_id=cid, status='blocked', route='llm', reasons=[str(exc)])
            interpretations[cid] = _rule_target(by_id[cid], sources, blocked=True)
            return []

    def review_batches(state):
        prepared = []
        for classify in (False, True):
            targets = [r.cell_id for r in routes if r.route == 'llm' and r.classify_role == classify]
            for start in range(0, len(targets), focused_evidence.MAX_TARGETS):
                prepared.extend(prepare_batch(targets[start:start + focused_evidence.MAX_TARGETS]))
        stop = False
        for number, (group, source_packet, packet, images, index, audit) in enumerate(prepared):
            batch_dir = run_dir / f'batch-{number:03d}'
            batch_dir.mkdir(exist_ok=True)
            if stop:
                for cid in group:
                    outcomes[cid] = dict(cell_id=cid, status='not_run', route='llm', reasons=['PREVIOUS_BATCH_FAILED'])
                    interpretations[cid] = _rule_target(by_id[cid], sources, blocked=True)
                continue
            for name, value in [('focused-evidence', source_packet), ('relation-evidence', packet), ('scope-audit', audit)]:
                pin(f'batch-{number:03d}/{name}.json', value)
            for i, image in enumerate(images):
                dest = batch_dir / f'focused-{i}.png'
                if not dest.exists():
                    dest.write_bytes(image['bytes'])

            def call(agent, payload):
                with trace.span(agent.ROLE.name, {'phase': 'semantic_review'}, generation=True):
                    r = agent.run(calls, payload, images, batch_dir)
                records.append(dict(**r, role=agent.ROLE.name, batch=number))
                if r.get('status') != 'completed' or not r.get('output'):
                    code = r.get('error_code') or r.get('error') or 'MODEL_OUTPUT_UNAVAILABLE'
                    safe_code = code if isinstance(code, str) and code.replace('_', '').isalnum() and code.isupper() else 'MODEL_OUTPUT_UNAVAILABLE'
                    raise ValueError(safe_code)
                return r['output']

            def classify_node(s):
                return dict(roles=call(role_classifier, source_packet))

            def infer_node(s):
                return dict(relations=call(relation_analyst, packet))

            def judge_node(s):
                return dict(judgment=call(relation_judge, {**packet, 'source_interpretation': s['relations']}))

            def verify_node(s):
                interpretation, validation = relations.validate(
                    packet, s.get('roles'), s['relations'], s['judgment'], index,
                    known_roles={cid: by_id[cid].role for cid in group})
                return dict(interpretation=interpretation, result=validation)

            try:
                inner = build_graph(classify_roles=classify_node, infer_relations=infer_node,
                    judge_relations=judge_node, validate_relations_and_storage=verify_node,
                    classify_required=routing[group[0]].classify_role)
                reviewed = inner.invoke({})
                check = reviewed['result']
                errors.extend(check['errors'])
                warnings.extend(check['warnings'])
                storage.extend(check['storage_checks'])
                missing.extend(check['missing_relations'])
                valid = not check['errors']
                if valid:
                    interpretations.update({t['cell_id']: t for t in reviewed['interpretation']['targets']})
                for cid in group:
                    outcomes[cid] = dict(cell_id=cid, route='llm',
                        status='llm_candidate' if valid and not check['warnings'] else 'llm_held',
                        reasons=check['errors'] + check['warnings'])
                    if not valid:
                        interpretations[cid] = _rule_target(by_id[cid], sources, blocked=True)
                batches.append(dict(batch=number, target_ids=group, validation=check,
                                    judgment=reviewed['judgment']))
                pin(f'batch-{number:03d}/review.json', reviewed)
            except InterruptedError:
                raise
            except Exception as exc:
                stop = True
                reasons = ['MODEL_REVIEW_INCOMPLETE']
                code = str(exc)
                if isinstance(exc, ValueError) and code.replace('_', '').isalnum() and code.isupper():
                    reasons.append(code)
                errors.extend(reasons)
                batches.append(dict(batch=number, target_ids=group, error=type(exc).__name__, reasons=reasons))
                for cid in group:
                    outcomes[cid] = dict(cell_id=cid, status='not_run', route='llm', reasons=reasons)
                    interpretations[cid] = _rule_target(by_id[cid], sources, blocked=True)
        return dict(batch_results=batches)

    def combine(state):
        progress('validate_relations_and_storage')
        if set(outcomes) != set(ids):
            raise ValueError('ROUTING_COVERAGE_MISMATCH')
        if any(o['status'] in ('blocked', 'not_run', 'llm_held') for o in outcomes.values()):
            warnings.append('UNRESOLVED_REVIEW_TARGETS')
        summary = dict(total=len(ids), rule_retained=sum(r.route == 'rule' for r in routes),
                       llm_requested=sum(r.route == 'llm' for r in routes),
                       llm_completed=sum(o['status'] in ('llm_candidate', 'llm_held') for o in outcomes.values()),
                       blocked=sum(o['status'] == 'blocked' for o in outcomes.values()),
                       unresolved=sum(o['status'] in ('blocked', 'not_run', 'llm_held') for o in outcomes.values()))
        return dict(interpretation=dict(summary='규칙 후보와 필요한 셀의 모델 검토를 결합한 결과',
                    targets=[interpretations[cid] for cid in ids], missing_evidence=[]),
                    routing_summary=summary, outcomes=[outcomes[cid] for cid in ids],
                    review_complete=not errors and summary['unresolved'] == 0,
                    result=dict(errors=sorted(set(errors)), warnings=sorted(set(warnings)),
                                storage_checks=storage, missing_relations=missing,
                                semantic_verified=False, training_ready=False,
                                verdict='held' if errors or warnings else 'source_supported_candidate'))

    workflow = StateGraph(table_review_state.State)
    for name, fn in [('semantic_candidates', prepare), ('semantic_gate', route),
                     ('review_uncertain', review_batches), ('combine_candidates', combine)]:
        workflow.add_node(name, fn)
    workflow.add_edge(START, 'semantic_candidates')
    workflow.add_edge('semantic_candidates', 'semantic_gate')
    workflow.add_conditional_edges('semantic_gate', lambda s: 'review_uncertain' if s['needs_model'] else 'combine_candidates')
    workflow.add_edge('review_uncertain', 'combine_candidates')
    workflow.add_edge('combine_candidates', END)
    state = workflow.compile().invoke({})
    trace.flush()
    result = dict(version=version.VERSION, **state, call_records=records,
                  api_calls=sum(r.get('api_calls', 0) for r in records),
                  estimated_cost_usd=None if any(r.get('estimated_cost_usd') is None for r in records)
                  else sum(r['estimated_cost_usd'] for r in records),
                  trace_id=trace.trace_id, semantic_verified=False, training_ready=False, parser_modified=False)
    pin('result.json', result)
    return result
