"""Shared execution steps for the original CLI and LangGraph. No second retriever."""
from . import repository
from .answer import generate
from .index import evidence_pack
from .telemetry import span


def resolve_evidence(hits, question):
    evidence = []
    seen_docs = set()
    for hit in hits:
        rid = hit['run_id']
        doc = repository.fetch_document(rid)
        if doc.document_id in seen_docs:
            continue
        seen_docs.add(doc.document_id)
        same = [h['payload'] for h in hits if h['run_id'] == rid]
        with span('table.resolve_cells', {'run_id': rid, 'question': question}) as record:
            pack = evidence_pack(doc, same, question, max_cells=8)
            for e in pack:
                e['evidence_id'] = f'E{len(evidence)+1}'
                e['run_id'] = rid
                evidence.append(e)
            record['output'] = {'evidence': pack}
        if len(seen_docs) >= 3:
            break
    return evidence


def validate_evidence(evidence, question):
    import re
    periods = re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', question)
    eligible = [e for e in evidence if e.get('lookup_eligible') is not False]
    # A report's publication date is not a table period. Only column paths qualify.
    value_question = bool(re.search(r'금액|얼마|구성비|비율|잔액|수치|총계|몇', question))
    missing = [p for p in periods if not any(p in ' '.join(e.get('column_path', []))
               or (not value_question and not e.get('table_id') and p in (e.get('value_raw') or ''))
               for e in eligible)]
    reasons = ([] if eligible else ['NO_ELIGIBLE_EVIDENCE']) + (['REQUESTED_PERIOD_NOT_IN_COLUMNS'] if missing else [])
    return dict(sufficient=not reasons, reasons=reasons, periods=periods,
                semantic_status='unvalidated_candidate', facts_published=0)


def generate_answer(question, evidence, output, *, verify_sql=False, **kwargs):
    if not evidence:
        return dict(question=question, answer='검색된 근거가 없습니다.', abstained=True,
                    model_calls=0, facts_published=0)
    with span('gpt-4.1.answer', {'question': question, 'evidence': evidence}, generation=True) as record:
        result = generate(question, evidence, output, **kwargs)
        record['output'] = {**result, 'selected': [e for e in evidence if e['evidence_id'] in result['selection']['evidence_ids']]}
    if verify_sql:
        ids = result['selection']['evidence_ids']
        for e in evidence:
            if e['evidence_id'] in ids and e.get('table_id'):
                rows = repository.sql_value_probe(e['run_id'], e['table_id'], e['cell_id'])
                if not rows or any(rows[0][sql] != e[field] for sql, field in (
                    ('value_raw', 'value_raw'), ('row_node_ids', 'row_node_ids'),
                    ('column_node_ids', 'column_node_ids'))):
                    raise ValueError('SELECTED_SQL_CELL_MISMATCH')
    return result


def legacy(question, output, *, run_id=None, limit=8, **kwargs):
    hits = repository.search(question, run_id, limit)
    evidence = resolve_evidence(hits, question)
    return dict(original_question=question, candidates=hits, evidence=evidence,
                validation=validate_evidence(evidence, question),
                result=generate_answer(question, evidence, output, **kwargs))
