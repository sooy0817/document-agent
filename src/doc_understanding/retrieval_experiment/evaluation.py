"""Reference use is confined to AFTER retrieval. Source membership is not accuracy."""
import statistics
import math


def score_case(case, state):
    reference = case['reference']
    ranked = [h['payload']['table_id'] for h in state.get('candidates', [])]
    relevant = set(reference['table_ids'])
    evidence = state.get('evidence', [])
    result = state.get('result', {})
    selection = result.get('selection', dict(abstained=True))
    answered = bool(evidence) and not selection['abstained'] and not state.get('errors')
    expected_answer = not reference['expected_abstained']
    cells_equal = answered and [e['cell_id'] for e in evidence] == reference['cell_ids']
    values_equal = answered and [e['value_raw'] for e in evidence] == reference['values_raw']
    path_equal = answered and [e['row_path'] for e in evidence] == reference.get('row_paths') and (
        [e['column_path'] for e in evidence] == reference.get('column_paths'))
    source_equal = answered and all(e['table_id'] in relevant and e['document_id'] == reference.get('document_id')
        and e['page'] == reference.get('page') and e['semantic_status'] == 'unvalidated_candidate'
        and e['unit_context'] for e in evidence)
    return dict(table_recall_at_5=len(set(ranked[:5]) & relevant)/len(relevant) if relevant else None,
        table_top1=bool(ranked and ranked[0] in relevant) if relevant else None,
        cell_exact_match=bool(cells_equal and values_equal) if expected_answer else None,
        evidence_correct=bool(cells_equal and values_equal and path_equal and source_equal) if answered else None,
        answered=answered, expected_abstained=reference['expected_abstained'],
        abstention_correct=not answered if reference['expected_abstained'] else None,
        group=case['group'], graph_error=bool(state.get('errors')))


def aggregate(records):
    def mean_field(name):
        values = [r['metrics'][name] for r in records if r['metrics'][name] is not None]
        return dict(value=sum(values)/len(values) if values else None, denominator=len(values))
    latencies = sorted(r['elapsed_seconds'] for r in records)
    return dict(cases=len(records), table_recall_at_5=mean_field('table_recall_at_5'),
        table_top1=mean_field('table_top1'), cell_exact_match=mean_field('cell_exact_match'),
        evidence_accuracy=mean_field('evidence_correct'), abstention_accuracy=mean_field('abstention_correct'),
        answered=sum(r['metrics']['answered'] for r in records),
        abstained=sum(not r['metrics']['answered'] for r in records),
        errors=sum(r['metrics']['graph_error'] for r in records),
        latency_seconds=dict(mean=statistics.mean(latencies), p50=statistics.median(latencies),
                             p95=latencies[max(0,math.ceil(len(latencies)*.95)-1)]),
        positive_abstentions=[r['case_id'] for r in records if not r['metrics']['expected_abstained'] and not r['metrics']['answered']],
        wrong_tables=[r['case_id'] for r in records if r['metrics']['table_top1'] is False],
        wrong_answers=[r['case_id'] for r in records if r['metrics']['evidence_correct'] is False])
