"""Explicit opt-in period contract experiment; immutable inputs and outputs."""
import argparse
import html
import json
import os
from pathlib import Path
from run_axis_judge import ROOT, read, write, hashes
from doc_understanding.agents.axis_review.axis_resolver import AxisRegion
from doc_understanding.parser.context.period_context import PeriodProposal
from doc_understanding.parser.context.period_decisions import VERSION, DecisionAdapter, PeriodDecisions, build_decision_graph, compare_decisions, legacy_decisions
from doc_understanding.runtime.telemetry import Trace, load_config
from doc_understanding.rag.observability.judge import publish_scores


def report(output, records, summary):
    esc=lambda x:html.escape(str(x))
    blocks=[]
    for n,r in enumerate(records):
        cells={c['id']:c['text'] for c in read(ROOT/r['case']['source'])['cells']}
        rows=[]
        for phase in ['baseline_comparison','comparison']:
            for t in (r.get(phase) or {}).get('targets',[]):
                rows.append('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in [phase,
                    cells[t['target_cell_id']],t['target_cell_id'].split('/')[-1],
                    t['proposed_status'],t['judge_status'],t['verdict'],t['reason']])+'</tr>')
        blocks.append(f'<section><h2>{esc(r["case"]["id"])} · {esc(r["case"]["split"])}</h2>'
            f'<p><a href="{esc((ROOT/r["case"]["source"]).as_uri())}">실제 원본 셀 JSON</a> · '
            f'<a href="region-{n:03d}.json">전체 실행 기록</a> · '
            f'<a href="region-{n:03d}-sidecar.json">새 상태·역할 JSON</a> · '
            f'<a href="{esc(r.get("trace_url") or "#")}">Langfuse</a></p>'
            f'<img width="850" style="max-width:100%" src="{esc((ROOT/r["case"]["image"]).as_uri())}">'
            '<table><tr><th>조건</th><th>원문 값</th><th>셀</th><th>생성 상태</th><th>Judge 상태</th><th>비교</th><th>근거</th></tr>'+''.join(rows)+'</table>'
            '<p>계약 검사: '+esc(r['proposal_errors'])+'</p><details open><summary>실제 새 JSON</summary><pre>'+esc(json.dumps(r['sidecar'],ensure_ascii=False,indent=2))+'</pre></details></section>')
    (output/'comparison.html').write_text('''<!doctype html><html lang="ko"><meta charset="utf-8"><title>기간 관계 상태·역할 분리</title>
<style>body{font:16px/1.7 system-ui,"Malgun Gothic";max-width:1250px;margin:30px auto;padding:20px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:8px;text-align:left;vertical-align:top}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:650px;overflow:auto;background:#f3f5f7;padding:15px}section{margin-top:40px;border-top:2px solid #ddd}a{overflow-wrap:anywhere}</style>
<h1>기간 관계 필요·불필요·근거 부족 / 설명과 적용 역할 분리</h1>
<p>원본·기존 결과 보존. required=관계 필요, not_needed=불필요, insufficient_evidence=근거 부족. 생성기와 독립 Judge에는 같은 원본 이미지·셀·대상 ID만 제공합니다. 참조 정답·이전 후보는 입력하지 않습니다.</p>
<p>이전 후보와 새 후보를 동일한 새 Judge 목록으로 비교합니다. 기존 판정도 원래 파일에 남깁니다. 모델 일치가 정답 검증은 아니며, 프롬프트 효과와 샘플링 변동은 분리할 수 없습니다. 기존 행·열 경로·원본 IR·DB·RAG는 변경하지 않았습니다.</p>'''+
        '<pre>'+esc(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre>'+''.join(blocks)+'</html>',encoding='utf-8')


def run(manifest,output,*,allow_external=False,max_calls=4,langfuse_config=None):
    if not allow_external: raise ValueError('EXPLICIT_LIVE_FLAG_REQUIRED')
    cases=read(manifest)['cases']
    if len(cases)*2>max_calls: raise ValueError('CALL_BUDGET_TOO_SMALL')
    loaded=[];paths=[manifest]
    for c in cases:
        region=AxisRegion.model_validate(read(ROOT/c['source']))
        from doc_understanding.parser.context.period_context import period_request
        period_request(region,c['target_ids'])
        loaded.append((c,region,(ROOT/c['image']).read_bytes()))
        paths += [ROOT/c['source'],ROOT/c['image']]
        if c.get('baseline'):paths.append(ROOT/c['baseline'])
    output.mkdir(parents=True,exist_ok=False)
    protected=paths+list((ROOT/'reports/period-context-20261030').rglob('*.json'))
    before=hashes(protected)
    write(output/'freeze.json',dict(inputs=before,code=hashes([
        ROOT/'src/doc_understanding/parser/context/period_context.py',
        ROOT/'src/doc_understanding/parser/context/period_decisions.py',Path(__file__)])))
    config=load_config(langfuse_config) if langfuse_config else None
    trace=Trace(config); adapter=DecisionAdapter(allow_external=True,max_calls=max_calls);records=[]
    for n,(case,region,img) in enumerate(loaded):
        tid=oid=None
        print(json.dumps(dict(case=case['id'],stage='start')),flush=True)
        graph=build_decision_graph(region,case['target_ids'],img,adapter,trace)
        if n==0:(output/'graph.mmd').write_text(graph.get_graph().draw_mermaid(),encoding='utf-8')
        with trace.span('period-decision-experiment',dict(run_id=output.name,source_cell_ids=case['target_ids'])) as box:
            if trace.client:
                try:tid=trace.client.get_current_trace_id();oid=trace.client.get_current_observation_id()
                except Exception:pass
            state=graph.invoke({},config={'recursion_limit':10})
            baseline=None; migrated=None
            if case.get('baseline') and state['inventory']:
                old=read(ROOT/case['baseline'])
                if old.get('proposal'):
                    migrated=legacy_decisions(PeriodProposal.model_validate(old['proposal']),case['target_ids'])
                    baseline=compare_decisions(region,case['target_ids'],migrated,PeriodDecisions.model_validate(state['inventory']))
            specs=[]
            for phase,res in [('baseline',baseline),('new',state['comparison'])]:
                if res is None:continue
                for i,t in enumerate(res['targets']):
                    prefix=f'period_decision.{phase}.t{i}'
                    comment=t['reason']+' | target='+t['target_cell_id']
                    for dim,value in [('generator_state',t['proposed_status']),('judge_state',t['judge_status']),('verdict',t['verdict'])]:
                        specs.append(dict(name=prefix+'.'+dim,value=value,data_type='CATEGORICAL',comment=comment))
                    for dim in ['completeness_score','scope_score']:
                        score=t[dim]
                        specs.append(dict(name=prefix+'.'+dim,value=float(score) if score is not None else 'unjudgeable',data_type='NUMERIC' if score is not None else 'CATEGORICAL',comment=comment))
            exported=publish_scores(trace,config,specs,tid,oid)
            box['output']=dict(status='completed',passed=state['comparison']['valid'],
                semantic_status='model_agreement_only',facts_published=0,
                validation=[dict(cell_id=t['target_cell_id'],status=t['verdict']) for t in state['comparison']['targets']])
        record=dict(**state,case=case,baseline_comparison=baseline,
            baseline_migration_view=migrated.model_dump(mode='json') if migrated else None,
            trace_id=tid,score_export=exported)
        if trace.client and tid:
            try:record['trace_url']=trace.client.get_trace_url(trace_id=tid)
            except Exception:pass
        write(output/f'region-{n:03d}.json',record)
        write(output/f'region-{n:03d}-sidecar.json',dict(**state['sidecar'],source_registry=case['source']))
        records.append(record);trace.flush()
        print(json.dumps(dict(case=case['id'],errors=state['proposal_errors'],results=[(t['proposed_status'],t['judge_status'],t['verdict']) for t in state['comparison']['targets']]),ensure_ascii=False),flush=True)
    ledger=[r[k] for r in records for k in ['proposal_record','inventory_record']]
    summary=dict(version=VERSION,regions=len(records),targets=sum(len(c['target_ids']) for c in cases),
        actual_api_attempts=adapter.calls,completed_calls=sum(m['status']=='completed' for m in ledger),
        estimated_cost_usd=sum(m.get('estimated_cost_usd') or 0 for m in ledger),
        unknown_cost_calls=sum(m.get('estimated_cost_usd') is None for m in ledger),
        inputs_unchanged=before==hashes(protected),trace_warnings=trace.warnings,
        semantic_verified=False,facts_published=0)
    write(output/'summary.json',summary);report(output,records,summary)
    print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--allow-external',action='store_true');p.add_argument('--max-calls',type=int,default=4)
    p.add_argument('--langfuse-config',type=Path);p.add_argument('--env-file',type=Path)
    args=vars(p.parse_args());env=args.pop('env_file')
    if env and args['allow_external']:
        for line in env.read_text(encoding='utf-8-sig').splitlines():
            if line.strip().startswith('OPENAI_API_KEY='):
                os.environ.setdefault('OPENAI_API_KEY',line.split('=',1)[1].strip().strip('\"\''))
    run(**args)
