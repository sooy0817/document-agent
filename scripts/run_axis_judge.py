"""Immutable evaluation of saved candidates. Explicit API opt-in, no generation/repair."""
import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import time
from run_axis_graph import ROOT, read, write
from doc_understanding.agents.axis_review.axis_resolver import AxisRegion
from doc_understanding.agents.axis_review.axis_generation import GeneratedStructure
from doc_understanding.agents.axis_review.axis_judge import AxisJudgeAdapter, JudgeResult, build_judge_graph, score_specs, judgement_summary, RUBRIC_VERSION, PROMPT
from doc_understanding.runtime.telemetry import Trace, load_config
from doc_understanding.rag.observability.judge import publish_scores, read_scores, verify_scores
from doc_understanding.rag.observability.trace_reader import read_observations


def hashes(paths):
    return {str(p.resolve()):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def report(output,summary,results):
    esc=lambda x:html.escape(str(x))
    sections=[]
    for r in results:
        grades=r.get('judgement',{}).get('grades',[]) if r.get('judgement') else []
        rows=''.join(f'<tr><td>{esc(g["dimension"])}</td><td>{esc(g["score"])}</td><td>{esc(g["reason"])}</td></tr>' for g in grades)
        sections.append(f'<h2>{esc(r["case_id"])} — {esc(r["label"])}</h2><p>기존 후보 값: {esc(r["value_raw"])} · Judge: {esc((r.get("judgement") or {}).get("verdict","unavailable"))} · 참조 판정: {esc(r["reference_verdict"])} · 판정 일치: {r["reference_agreement"]}</p>'
            f'<p>기존 행: {esc(r["row_labels"])} / 열: {esc(r["column_labels"])}</p>'
            f'<p><a href="{esc(r.get("trace_url") or "#")}">Langfuse Trace / Scores</a> · <a href="{r["case_id"]}.json">전체 Judge 결과</a></p>'
            f'<img style="max-width:100%;width:850px" src="{esc(Path(r["image_path"]).as_uri())}">'
            f'<table><tr><th>항목</th><th>0~2점</th><th>모델 판정 근거</th></tr>{rows}</table>'
            f'<details><summary>셀 ID 및 원본 근거</summary><pre>{esc(json.dumps(r.get("judgement"),ensure_ascii=False,indent=2))}</pre></details>')
    content='''<!doctype html><meta charset="utf-8"><title>Axis LLM Judge — 개발 사례 평가</title>
<style>body{font-family:Arial,sans-serif;max-width:1150px;margin:35px auto;line-height:1.6;padding:20px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #bbb;padding:10px;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere}h2{margin-top:55px}</style>
<h1>LLM Judge: 기존 오류·정상 사례 평가</h1>
<p>기존 생성 출력은 고정했습니다. Judge에는 Gold·기존 판정·생성 이유를 주지 않았습니다. 개발 사례 5건이며 독립 일반화 성능이 아닙니다. 점수는 모델 평가일 뿐 의미 승인·원본 수정·자동 학습이 아닙니다. 0 잘못됨 / 1 불완전 / 2 일치 / null 판단 불가.</p>'''
    (output/'comparison.html').write_text(content+'<pre>'+esc(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre>'+''.join(sections),encoding='utf-8')


def run(manifest,output,*,allow_external=False,max_calls=5,langfuse_config=None):
    if not allow_external: raise ValueError('EXPLICIT_LIVE_FLAG_REQUIRED')
    cases=read(manifest)['cases']
    if len(cases)>max_calls: raise ValueError('CALL_BUDGET_TOO_SMALL')
    # Fail before paying if a manifest is invalid. Reference labels never enter model input.
    loaded=[]; paths=[manifest]
    for c in cases:
        source=ROOT/c['source']; generated=ROOT/c['generated']; image=ROOT/c['image']
        region=AxisRegion.model_validate(read(source)); saved=read(generated)
        g=GeneratedStructure.model_validate(saved['generated'])
        from doc_understanding.agents.axis_review.axis_judge import judge_request
        judge_request(region,g,c['target_id'])
        loaded.append((c,region,g,image.read_bytes()))
        paths.extend([source,generated,image])
    original_hashes=hashes(paths)
    output.mkdir(parents=True,exist_ok=False)
    code_paths=[Path(__file__),ROOT/'src/doc_understanding/agents/axis_review/axis_judge.py',
                ROOT/'src/doc_understanding/rag/observability/judge.py',
                ROOT/'src/doc_understanding/agents/axis_review/axis_generation.py']
    write(output/'freeze.json',dict(source_hashes=original_hashes,code_hashes=hashes(code_paths),
        rubric_version=RUBRIC_VERSION,prompt_sha256=hashlib.sha256(PROMPT.encode()).hexdigest(),
        split='development_calibration',planned_cases=len(cases),planned_max_calls=max_calls))
    config=load_config(langfuse_config) if langfuse_config else None
    trace=Trace(config); adapter=AxisJudgeAdapter(allow_external=True,max_calls=max_calls)
    results=[]
    for case,region,g,image in loaded:
        trace_id=None
        with trace.span('axis-judge.case',dict(run_id=case['id'],method=RUBRIC_VERSION)) as root:
            if trace.client:
                try: trace_id=trace.client.get_current_trace_id()
                except Exception: trace.warnings.append('LANGFUSE_CONTEXT_FAILED')
            graph=build_judge_graph(region,g,case['target_id'],
                lambda:adapter.judge(region,g,case['target_id'],image),trace)
            if not (output/'graph.mmd').exists():
                (output/'graph.mmd').write_text(graph.get_graph().draw_mermaid(),encoding='utf-8')
            state=graph.invoke({},config={'recursion_limit':10})
            judge=JudgeResult.model_validate(state['judgement']) if state.get('judgement') else None
            valid=state['judge_validation']['valid']
            agreement=judge.verdict==case['reference_verdict'] if valid else None
            spec=score_specs(judge) if valid else [dict(name='axis_judge.status',value='invalid_or_unavailable',
                data_type='CATEGORICAL',comment=','.join(state['judge_validation']['errors']))]
            spec.append(dict(name='axis_hard_checks',value=float(not state['deterministic_check']['errors']),
                data_type='BOOLEAN',comment='Source ID/geometry checks only; not semantic correctness.'))
            if agreement is not None:
                spec.append(dict(name='axis_reference_agreement',value=float(agreement),data_type='BOOLEAN',
                    comment='Development reference comparison AFTER inference; not independent holdout.'))
            with trace.span('record_evaluation',dict(run_id=case['id'])) as box:
                score_record=publish_scores(trace,config,spec,trace_id,state.get('candidate_observation_id'))
                box['output']=dict(status=score_record['status'],passed=agreement,
                    semantic_status='review_required',facts_published=0)
            root['output']=dict(result=judgement_summary(judge),passed=agreement,semantic_status='review_required')
        cells={c.id:c for c in region.cells};p=next(p for p in g.links if p.cell_id==case['target_id'])
        record=dict(**state,case_id=case['id'],label=case['label'],reference_verdict=case['reference_verdict'],
            reference_agreement=agreement,reference_source=case['reference_source'],
            value_raw=cells[p.cell_id].text,row_labels=[cells[i].text for i in p.row_path],
            column_labels=[cells[i].text for i in p.column_path],image_path=str((ROOT/case['image']).resolve()),
            trace_id=trace_id,trace_url=None,score_export=score_record)
        if trace.client and trace_id:
            try: record['trace_url']=trace.client.get_trace_url(trace_id=trace_id)
            except Exception: trace.warnings.append('LANGFUSE_URL_FAILED')
        write(output/(case['id']+'.json'),record);results.append(record)
        trace.flush()
        print(json.dumps(dict(case=case['id'],verdict=judge.verdict if judge else None,
            agreement=agreement,usage=state['model_record'].get('usage'),
            cost=state['model_record'].get('estimated_cost_usd')),ensure_ascii=False),flush=True)
    checks=[]
    for r in results:
        check=dict(case_id=r['case_id'],trace_id=r['trace_id'],verified=False)
        if trace.client and r['trace_id']:
            for attempt in range(3):
                try:
                    obs=read_observations(trace.client,config,r['trace_id'])
                    scores=read_scores(config,r['trace_id'])
                    score_check=verify_scores(r['score_export']['scores'],scores)
                    names={o.get('name') for o in obs['data']}
                    expected={'axis-judge.case','source_candidate','deterministic_check','judge_structure','validate_judgement','record_evaluation'}
                    check.update(verified=score_check['verified'] and expected<=names,
                        score_verification=score_check,observations=obs['data'],scores=scores,read_api=obs.get('read_api'))
                    if check['verified']: break
                except Exception as exc: check['error']=type(exc).__name__
                if attempt<2: time.sleep(2)
        checks.append(check)
    write(output/'langfuse-readback.json',checks)
    summary=dict(cases=len(results),split='development_calibration',actual_api_attempts=adapter.calls,
        completed_calls=sum(r['model_record']['status']=='completed' for r in results),
        reference_agreements=sum(r['reference_agreement'] is True for r in results),
        invalid_or_unavailable=sum(r['reference_agreement'] is None for r in results),
        wrong_cases_flagged=sum(r['reference_verdict']=='incorrect' and r['reference_agreement'] is True for r in results),
        wrong_cases_total=sum(r['reference_verdict']=='incorrect' for r in results),
        correct_cases_retained=sum(r['reference_verdict']=='correct' and r['reference_agreement'] is True for r in results),
        estimated_cost_usd=sum(r['model_record'].get('estimated_cost_usd') or 0 for r in results),
        unknown_cost_calls=sum(r['model_record'].get('estimated_cost_usd') is None for r in results),
        langfuse_verified_cases=sum(c['verified'] for c in checks),telemetry_warnings=trace.warnings,
        originals_unchanged=hashes(paths)==original_hashes,code_unchanged=hashes(code_paths)==read(output/'freeze.json')['code_hashes'],
        semantic_verified=False,facts_published=0,training_performed=False)
    write(output/'summary.json',summary);report(output,summary,results)
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--allow-external',action='store_true');parser.add_argument('--max-calls',type=int,default=5)
    parser.add_argument('--langfuse-config',type=Path);parser.add_argument('--env-file',type=Path)
    args=vars(parser.parse_args());env=args.pop('env_file')
    if env and args['allow_external']:
        for line in env.read_text(encoding='utf-8-sig').splitlines():
            if line.strip().startswith('OPENAI_API_KEY='):
                os.environ.setdefault('OPENAI_API_KEY',line.split('=',1)[1].strip().strip('\"\''))
    run(**args)
