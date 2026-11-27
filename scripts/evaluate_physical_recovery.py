# 역할: 물리 복원 평가 CLI의 입력 로딩·전략별 집계·결과 저장을 담당한다. 채점은 evaluation.parser에 위임한다.
"""Read-only evaluation. Source reference sidecars never reach the parser."""
import argparse
import json
from doc_understanding.evaluation.parser.physical_recovery import evaluate
from pathlib import Path


def read(p):
    return json.loads(Path(p).read_text(encoding='utf8'))


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--run',type=Path,required=True)
    ap.add_argument('--references',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    if args.output.exists():
        raise ValueError('IMMUTABLE_EVALUATION_EXISTS')
    refs=read(args.references)
    rows=[]
    for result in read(args.run/'results.json')['results']:
        path=Path(result['output'])
        for case in refs['cases']:
            if case['source_id']!=result['id']:
                continue
            row=evaluate(case,read(path/f'page-{case["page"]:03}-physical.json'),read(path/f'page-{case["page"]:03}-structured.json'))
            rows.append(dict(strategy=result['strategy'],**row))
    summary=[]
    for split in sorted({r['split'] for r in rows}):
        for strategy in sorted({r['strategy'] for r in rows}):
            local=[r for r in rows if r['split']==split and r['strategy']==strategy]
            probes=[p for r in local for p in r['probes']]
            cells=[c for r in local for c in r['full_cell_checks']]
            summary.append(dict(split=split,strategy=strategy,tables=len(local),detected=sum(r['detected'] for r in local),
                row_count_exact=sum(r['row_count_exact'] is True for r in local),row_count_denominator=sum(r['row_count_exact'] is not None for r in local),
                column_count_exact=sum(r['column_count_exact'] is True for r in local),column_count_denominator=sum(r['column_count_exact'] is not None for r in local),
                full_cells=dict(denominator=len(cells),exact=sum(c['matched'] for c in cells),row=sum(c['row'] for c in cells),column=sum(c['column'] for c in cells),span=sum(c['span'] for c in cells)),
                probes=dict(denominator=len(probes),cell_exact=sum(p['cell_exact'] for p in probes),row_path=sum(p['row_path_exact'] for p in probes),column_path=sum(p['column_path_exact'] for p in probes),
                    safe_mapping=sum(p['cell_exact'] and p['row_path_exact'] and p['column_path_exact'] and p['mapping_state']!='unresolved' for p in probes),
                    wrong_values=sum(p['wrong_value_at_source'] for p in probes),wrong_row_parents=sum(p['wrong_row_parent'] for p in probes),wrong_column_parents=sum(p['wrong_column_parent'] for p in probes)),
                false_boundary_segments=sum(r['false_boundary_segments_in_full_references'] for r in local),
                extra_wrong_cells_full_reference_tables=sum(r['extra_or_wrong_cells'] or 0 for r in local)))
    with args.output.open('x',encoding='utf8') as f:
        json.dump(dict(reference_policy=refs['policy'],summary=summary,cases=rows),f,ensure_ascii=False,indent=2)
    print(json.dumps(summary))


if __name__=='__main__':
    main()
