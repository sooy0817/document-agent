"""Evaluate frozen Canonical paths with the existing Judge B; never repair them."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'scripts')]
from run_generalization_review import read,write,frozen
from run_period_expansion import inventory
from doc_understanding.agents.axis_review.axis_resolver import AxisRegion
from doc_understanding.agents.axis_review.axis_generation import GeneratedStructure
from doc_understanding.agents.axis_review.axis_judge_b import ComparisonJudgeAdapter, validate_explicit
from doc_understanding.agents.axis_review.axis_judge import judgement_summary, score_specs, judge_request
from doc_understanding.runtime.telemetry import Trace,load_config
from doc_understanding.rag.observability.judge import publish_scores


def prepare(baseline,output):
    import pymupdf
    manifest=read(baseline/'selection-copy.json')
    if not frozen(manifest):raise ValueError('FROZEN_CODE_CHANGED')
    output.mkdir(parents=True,exist_ok=False)
    cases=[]
    for record in read(baseline/'manifest.json')['records']:
        if record['status']!='completed':continue
        document=read(Path(record['viewer_folder'])/'document.json')
        ir,regions,_=inventory(Path(record['run'])/'document-ir.json')
        with pymupdf.open(Path(record['run'])/'source.pdf') as pdf:
            for c in record['selected_cases']:
                case=dict(id=c['id'],job_id=record['job_id'],viewer_folder=record['viewer_folder'],page=c['page'])
                candidates=c['anchor_matches']
                if not candidates:
                    cases.append(dict(**case,status='no_anchor_cell',reason=c['first_observation']));continue
                # Evaluation target binding only. References/expected paths never enter requests.
                target=min(candidates,key=lambda x:(len(x['text'] or ''),x['bbox']['y0'],x['bbox']['x0']))
                case.update(target_id=target['id'],binding='shortest_source_anchor_match_then_reading_order',
                            alternative_anchor_ids=[x['id'] for x in candidates],target_text=target['text'])
                region=next((r for r in regions if any(x.id==target['id'] for x in r.cells)),None)
                if region is None:
                    cases.append(dict(**case,status='no_source_region'));continue
                st=next(t for t in document['table_structure'] if t['table_id']==target['table_id'])
                mapping=next((m for m in st['cell_mappings'] if m['cell_id']==target['id']),None)
                nodes={n['node_id']:n for n in st['row_tree']+st['column_tree']}
                rp=[nodes[n]['source_cell_id'] for n in mapping['row_path']] if mapping else []
                cp=[nodes[n]['source_cell_id'] for n in mapping['column_path']] if mapping else []
                roles={r['cell_id']:r['role'] for t in document['table_structure'] for r in t['cell_roles']}
                generated=GeneratedStructure.model_validate(dict(roles=[dict(cell_id=x.id,roles=[roles.get(x.id,'unknown')],
                    evidence_cell_ids=[x.id],reason='Copied unchanged from baseline cell role') for x in region.cells],
                    links=[dict(cell_id=target['id'],row_path=rp,column_path=cp,
                        row_axis='present' if rp else 'unknown',column_axis='present' if cp else 'unknown',
                        evidence_cell_ids=list(dict.fromkeys([target['id']]+rp+cp)),reason='Frozen baseline; empty paths do not prove absence')]))
                folder=output/c['id'];folder.mkdir()
                write(folder/'region.json',region.model_dump(mode='json'))
                write(folder/'candidate.json',generated.model_dump(mode='json'))
                image=pdf[c['page']-1].get_pixmap(matrix=pymupdf.Matrix(1.5,1.5),clip=pymupdf.Rect(region.bbox.xyxy)+(-4,-4,4,4)).tobytes('png')
                (folder/'source.png').write_bytes(image)
                try:
                    _,request=judge_request(region,generated,target['id'])
                    if len(region.cells)>180 or len(json.dumps(request,ensure_ascii=False).encode())>100000 or len(image)>4000000:
                        raise ValueError('INPUT_BUDGET_EXCEEDED')
                    case.update(status='ready',folder=str(folder.resolve()))
                except ValueError as e:case.update(status='ineligible',reason=str(e),folder=str(folder.resolve()))
                cases.append(case)
    write(output/'cases.json',dict(cases=cases,baseline=str(baseline.resolve()),
        code_freeze=manifest['code_freeze'],reference_answers_sent=False,coverage='one_source_anchor_per_selected_region_not_all_cells'))
    print(json.dumps(dict(cases=len(cases),eligible=sum(c['status']=='ready' for c in cases)),ensure_ascii=False))


def live(prepared,output,env_file,config_path,max_calls):
    plan=read(prepared/'cases.json')
    if not frozen(plan):raise ValueError('FROZEN_CODE_CHANGED')
    eligible=[c for c in plan['cases'] if c['status']=='ready']
    if len(eligible)>max_calls:raise ValueError('CALL_BUDGET_TOO_SMALL')
    for line in env_file.read_text(encoding='utf-8-sig').splitlines():
        if line.strip().startswith('OPENAI_API_KEY='):
            os.environ.setdefault('OPENAI_API_KEY',line.split('=',1)[1].strip().strip('\"\''))
    output.mkdir(parents=True,exist_ok=False)
    config=load_config(config_path);trace=Trace(config)
    def execute(c):
        folder=Path(c['folder']);region=AxisRegion.model_validate(read(folder/'region.json'))
        candidate=GeneratedStructure.model_validate(read(folder/'candidate.json'))
        adapter=ComparisonJudgeAdapter(allow_external=True,max_calls=1)
        tid=oid=None
        with trace.span('new-korean-pdf-frozen-axis-review',dict(run_id=c['id'],mode='baseline_candidate_judge_B',
                document_id=region.document_id,page=c['page'],cell_id=c['target_id'])) as root:
            if trace.client:
                tid=trace.client.get_current_trace_id();oid=trace.client.get_current_observation_id()
            with trace.span('frozen_candidate',dict(cell_id=c['target_id'])) as box:
                link=candidate.links[0]
                box['output']=dict(cell_id=link.cell_id,row_path=link.row_path,column_path=link.column_path,
                                   value_raw=c['target_text'],semantic_status='unverified_baseline')
            with trace.span('judge_B',generation=True) as box:
                result,record=adapter.judge(region,candidate,c['target_id'],(folder/'source.png').read_bytes(),'B')
                box['output']=dict(**judgement_summary(result),usage=record.get('usage') or {},
                                   estimated_cost_usd=record.get('estimated_cost_usd') or 0)
            valid=validate_explicit(region,candidate,c['target_id'],result)
            with trace.span('validate_judge_output') as box:
                box['output']=dict(status='accepted_model_opinion' if valid['valid'] else 'invalid_model_output',errors=valid['errors'])
            exported=publish_scores(trace,config,score_specs(result),tid,oid) if result and valid['valid'] else dict(status='not_exported_invalid_or_missing')
            root['output']=dict(status=result.verdict if result else 'judge_failed',semantic_status='human_review_pending',facts_published=0)
        data=dict(case=c,judge=result.model_dump(mode='json') if result else None,validation=valid,
                  record=record,trace_id=tid,trace_url=trace.client.get_trace_url(trace_id=tid) if trace.client and tid else None,
                  score_export=exported,semantic_verified=False)
        write(output/(c['id']+'.json'),data)
        print(json.dumps(dict(id=c['id'],status=record['status'],verdict=result.verdict if result else None,valid=valid['valid']),ensure_ascii=False),flush=True)
        return data
    records=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks={pool.submit(execute,c):c for c in eligible}
        for future in as_completed(tasks):
            try:records.append(future.result())
            except Exception as e:
                c=tasks[future];write(output/(c['id']+'-failure.json'),dict(case=c,error=type(e).__name__,reason=str(e)[:180]))
                print(c['id']+' failed '+type(e).__name__,flush=True)
    trace.flush()
    ledger=[r['record'] for r in records]
    summary=dict(cases_denominator=len(plan['cases']),eligible=len(eligible),actual_api_attempts=len(ledger),
        completed_calls=sum(r['status']=='completed' for r in ledger),
        estimated_cost_usd=sum(r.get('estimated_cost_usd') or 0 for r in ledger),
        unknown_cost_calls=sum(r.get('estimated_cost_usd') is None for r in ledger),
        source_code_unchanged=frozen(plan),trace_warnings=trace.warnings,
        semantic_verified=False,whole_table_accuracy=None,skipped=[c for c in plan['cases'] if c['status']!='ready'])
    write(output/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--baseline',type=Path);p.add_argument('--prepared',type=Path)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--allow-external',action='store_true')
    p.add_argument('--env-file',type=Path);p.add_argument('--langfuse-config',type=Path);p.add_argument('--max-calls',type=int,default=20)
    a=p.parse_args()
    if a.baseline:prepare(a.baseline,a.output)
    elif a.allow_external:live(a.prepared,a.output,a.env_file,a.langfuse_config,a.max_calls)
    else:p.error('Live mode requires --allow-external')
