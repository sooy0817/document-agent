"""Compare period relations without changing source or saved axes. Live calls are explicit."""
import argparse
import html
import json
import os
from pathlib import Path

from run_axis_judge import read, write, hashes, ROOT
from doc_understanding.agents.axis_review.axis_resolver import AxisRegion, digest
from doc_understanding.agents.axis_review.axis_generation import GeneratedStructure
from doc_understanding.parser.context.period_context import PeriodProposal, PeriodInventory, PeriodTarget, build_period_graph, PeriodContextAdapter, compare_period_context, VERSION
from doc_understanding.runtime.telemetry import Trace, load_config
from doc_understanding.rag.observability.judge import publish_scores, read_scores, verify_scores
from doc_understanding.rag.observability.trace_reader import read_observations


def experiment_groups(manifest):
    """Manifest selects targets only; reference fields never enter inference."""
    groups = {}
    for case in read(manifest)['cases']:
        key = (case['source'], case['generated'], case['image'])
        groups.setdefault(key, []).append(case['target_id'])
    return list(groups.items())


def make_report(output, records, summary):
    esc = lambda v: html.escape(str(v))
    sections = []
    for n, r in enumerate(records):
        cells = {c['id']: c for c in read(ROOT/r['source'])['cells']}
        raw = lambda i: cells.get(i, {}).get('text', '[unknown ID]')
        rows = []
        for phase in ['before', 'after', 'scope_probe']:
            result = r.get(phase)
            if not result: continue
            for t in result['targets']:
                rows.append('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in [phase,
                    cells[t['target_cell_id']]['text'], t['target_cell_id'].split('/')[-1],
                    t['completeness_score'], t['scope_score'], t['verdict'],
                    ', '.join(cells[i]['text'] for x in t['missing'] or [] for i in x['source_cell_ids']),
                    ', '.join(cells[i]['text'] for x in t['extra'] or [] for i in x['source_cell_ids'])])+'</tr>')
        relations = []
        for rel in (r.get('proposal') or {}).get('relations', []):
            relations.append(dict(source=[dict(id=i, text=raw(i)) for i in rel['source_cell_ids']],
                applies_to=[dict(row=dict(id=p['row_cell_id'],text=raw(p['row_cell_id'])),
                    value=dict(id=p['value_cell_id'],text=raw(p['value_cell_id']))) for p in rel['applies_to']],
                reason=rel['reason']))
        sections.append(f'<section><h2>실행 {n+1} · {esc(r["source"])}</h2>'
            f'<p><a href="{esc((ROOT/r["source"]).as_uri())}">원본 셀 JSON</a> · '
            f'<a href="{esc((ROOT/r["generated"]).as_uri())}">변경하지 않은 행·열 후보</a> · '
            f'<a href="region-{n:03d}.json">실행 전체 JSON</a> · '
            f'<a href="region-{n:03d}-sidecar.json">기간 관계 sidecar</a> · '
            f'<a href="{esc(r.get("trace_url", "#"))}">Langfuse Trace</a></p>'
            f'<img src="{esc((ROOT/r["image"]).as_uri())}" alt="원본 영역" style="max-width:100%;width:850px">'
            '<h3>실제 생성된 관계</h3><pre>'+esc(json.dumps(relations,ensure_ascii=False,indent=2))+'</pre>'
            '<h3>동일 Judge 근거를 재생한 전후 비교</h3><table><tr><th>조건</th><th>원문 값</th><th>셀</th><th>완전성</th><th>범위</th><th>판정</th><th>누락 근거</th><th>추가 근거</th></tr>'+''.join(rows)+'</table>'
            '<p>before: 기간 연결 없음 / after: 실제 생성된 관계 / scope_probe: 적용 범위를 일부러 확장한 음성 대조군. '
            'scope_probe는 원래 파서 오류나 자연 발생 실패가 아닙니다. 완전성·범위는 기간 관계만 평가하며 기존 행·열 오류를 고치거나 승인하지 않습니다.</p>'
            '<details><summary>Judge가 독립적으로 찾은 원본 근거</summary><pre>'+esc(json.dumps(r.get('inventory'),ensure_ascii=False,indent=2))+'</pre></details>'
            '<p>생성 검사 오류: '+esc(r['proposal_errors'])+' / 비교 검사 오류: '+esc(r['after']['errors'])+'</p></section>')
    text='''<!doctype html><html lang="ko"><meta charset="utf-8"><title>기간 설명 연결 전후 비교</title>
<style>body{font:16px/1.65 system-ui,"Malgun Gothic",sans-serif;max-width:1200px;margin:30px auto;padding:20px;color:#22313d}td,th{border:1px solid #cbd2d8;padding:9px;text-align:left;vertical-align:top}table{border-collapse:collapse;width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f4f6;padding:18px;max-height:550px;overflow:auto}section{margin-top:45px;border-top:2px solid #cbd2d8}a{overflow-wrap:anywhere;color:#12629b}</style>
<h1>기간 설명 연결 · 원본 기반 Judge 비교</h1><p>기존 IR·행열 후보·이전 결과를 보존한 개발 실험입니다. 생성기와 Judge는 원본 이미지·셀 목록·대상 ID만 받습니다. Judge에는 생성된 관계나 정답 참조를 주지 않습니다. 하나의 Judge 근거 목록을 전후에 동일하게 재생해 모델 응답 변동을 분리했습니다. 사람 검증·독립 일반화 평가·RAG 답변 개선 완료를 뜻하지 않습니다.</p>
<p>흐름: 기간 관계 생성 → ID·근거 검사 → 독립 Judge의 원본 관계 목록 → 연결 전/후/범위 확장 비교. 기존 행·열 Judge는 그대로이며, 이 경로는 별도 opt-in 실험입니다.</p>'''
    (output/'comparison.html').write_text(text+'<pre>'+esc(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre>'+''.join(sections)+'</html>',encoding='utf-8')


def run(manifest, output, *, allow_external=False, max_calls=4, langfuse_config=None, replay_run=None):
    if not allow_external and not replay_run: raise ValueError('EXPLICIT_LIVE_FLAG_REQUIRED')
    if allow_external and replay_run: raise ValueError('LIVE_AND_REPLAY_ARE_EXCLUSIVE')
    groups = experiment_groups(manifest)
    if not replay_run and len(groups)*2 > max_calls: raise ValueError('CALL_BUDGET_TOO_SMALL')
    loaded = []
    paths = [manifest]
    for (source, generated, image), focus in groups:
        region = AxisRegion.model_validate(read(ROOT/source))
        g = GeneratedStructure.model_validate(read(ROOT/generated)['generated'])
        by_id = {c.id:c for c in region.cells}
        control = next((p for p in g.links if p.cell_id not in focus and p.row_path
                        and p.cell_id in by_id and by_id[p.cell_id].physical_usable
                        and all(i in by_id and by_id[i].physical_usable for i in p.row_path)), None)
        # A manifest can contain one normal target. Probe only groups with multiple targets.
        control = control if len(focus)>1 else None
        targets = focus + ([control.cell_id] if control else [])
        if not all(any(p.cell_id==i for p in g.links) for i in targets): raise ValueError('SAVED_TARGET_LINK_MISSING')
        loaded.append((source,generated,image,region,g,focus,targets,control))
        paths.extend(ROOT/p for p in (source,generated,image))
    output.mkdir(parents=True,exist_ok=False)
    protected = paths + list((ROOT/'reports/axis-judge-b-20261029').rglob('*.json')) + list((ROOT/'reports/axis-reference-spec-20261030').rglob('*.json'))
    before = hashes(protected)
    write(output/'freeze.json',dict(inputs=before,code=hashes([
        ROOT/'src/doc_understanding/parser/context/period_context.py',Path(__file__),
        ROOT/'src/doc_understanding/agents/axis_review/axis_judge.py',
        ROOT/'src/doc_understanding/agents/axis_review/axis_judge_b.py'])))
    config=load_config(langfuse_config) if langfuse_config else None
    trace=Trace(config);adapter=PeriodContextAdapter(allow_external=True,max_calls=max_calls)
    records=[]
    for n,(source,generated,image,region,g,focus,targets,control) in enumerate(loaded):
        print(json.dumps(dict(stage='start',region=n,targets=len(targets))),flush=True)
        saved_axes=digest(g);tid=oid=None
        image_bytes=(ROOT/image).read_bytes()
        if replay_run:
            saved=read(replay_run/f'region-{n:03d}.json')
            class ReplayAdapter:
                replay=True
                def call(self, reg, ids, img, stage):
                    import hashlib
                    from doc_understanding.parser.context.period_context import period_request
                    key='proposal' if stage=='propose' else 'inventory'
                    record=saved[key+'_record']
                    _,request=period_request(reg,ids)
                    if (record['source_sha256']!=digest(reg) or record['request']!=json.loads(json.dumps(request))
                            or record['image_sha256']!=hashlib.sha256(img).hexdigest()):
                        raise ValueError('REPLAY_SOURCE_OR_TARGET_MISMATCH')
                    cls=PeriodProposal if stage=='propose' else PeriodInventory
                    value=cls.model_validate(saved[key]) if saved[key] else None
                    return value,{**record,'usage':None,'estimated_cost_usd':0,'status':'replayed',
                                  'replay_original_record':str(replay_run/f'region-{n:03d}.json')}
            selected_adapter=ReplayAdapter()
        else:selected_adapter=adapter
        graph=build_period_graph(region,targets,image_bytes,selected_adapter,trace)
        if n==0:(output/'graph.mmd').write_text(graph.get_graph().draw_mermaid(),encoding='utf-8')
        with trace.span('period-context-comparison',dict(run_id=output.name,source_cell_ids=targets)) as box:
            if trace.client:
                try:tid=trace.client.get_current_trace_id();oid=trace.client.get_current_observation_id()
                except Exception:pass
            state=graph.invoke({},config={'recursion_limit':10})
            probe=None;probe_proposal=None
            if control and state['proposal'] and not state['proposal_errors'] and state['inventory']:
                p=PeriodProposal.model_validate(state['proposal']);inv=PeriodInventory.model_validate(state['inventory'])
                if p.relations:
                    relation=p.relations[0]
                    pair=PeriodTarget(row_cell_id=control.row_path[-1],value_cell_id=control.cell_id)
                    newrel=relation.model_copy(update=dict(applies_to=relation.applies_to+[pair],
                        evidence_cell_ids=list(dict.fromkeys(relation.evidence_cell_ids+[pair.row_cell_id,pair.value_cell_id]))))
                    # The negative control asserts a wrong relation; it must not also abstain
                    # on that same target, which would test a schema conflict instead of scope.
                    probe_proposal=p.model_copy(update={'relations':[newrel]+p.relations[1:],
                        'unknown_target_ids':[i for i in p.unknown_target_ids if i!=control.cell_id]})
                    probe=compare_period_context(region,targets,probe_proposal,inv)
                    with trace.span('synthetic_scope_probe',dict(cell_id=control.cell_id,kind='negative_control')) as probe_box:
                        probe_box['output']=dict(passed=probe['valid'],errors=probe['errors'],validation=[
                            dict(cell_id=t['target_cell_id'],score=t['scope_score'],status=t['verdict'],reason=t['reason']) for t in probe['targets']])
            specs=[]
            for phase,result in [('before',state['before']),('after',state['after']),('scope_probe',probe)]:
                if not result:continue
                if not result['valid']:
                    specs.append(dict(name='period.'+phase+'.status',value='invalid_or_unavailable',data_type='CATEGORICAL',comment=','.join(result['errors'])))
                for idx,t in enumerate(result['targets']):
                    for dim in ['completeness','scope']:
                        score=t[dim+'_score'];name=f'period.{phase}.target{idx}.{dim}'
                        specs.append(dict(name=name,value=float(score) if score is not None else 'unjudgeable',data_type='NUMERIC' if score is not None else 'CATEGORICAL',comment=t['reason']+' | target='+t['target_cell_id']))
            exported=publish_scores(trace,config,specs,tid,oid)
            box['output']=dict(status='completed',passed=state['after']['valid'],semantic_status='candidate_only',facts_published=0)
        r=dict(**state,scope_probe=probe,scope_probe_proposal=probe_proposal.model_dump(mode='json') if probe_proposal else None,
            source=source,generated=generated,image=image,focus_target_ids=focus,control_target_id=control.cell_id if control else None,
            trace_id=tid,score_export=exported,axes_unchanged=saved_axes==digest(g),
            judge_input_is_source_only=True,same_inventory_replayed=True,semantic_verified=False,facts_published=0)
        if trace.client and tid:
            try:r['trace_url']=trace.client.get_trace_url(trace_id=tid)
            except Exception:pass
        write(output/f'region-{n:03d}.json',r)
        refs=set()
        for rel in (state['proposal'] or {}).get('relations',[]):refs.update(rel['evidence_cell_ids'])
        write(output/f'region-{n:03d}-sidecar.json',dict(schema_version=VERSION,status='candidate',source_sha256=digest(region),
            source_registry=source,axis_candidate_ref=generated,period_relations=(state['proposal'] or {}).get('relations',[]),
            source_cells=[c.model_dump(mode='json') for c in region.cells if c.id in refs],
            validation_errors=state['proposal_errors'],semantic_verified=False,approved_for_training=False,facts_published=0))
        trace.flush();records.append(r)
        print(json.dumps(dict(stage='completed',region=n,proposal_errors=state['proposal_errors'],
            before=state['before'],after=state['after']),ensure_ascii=False),flush=True)
    readback=[]
    if trace.client:
        for r in records:
            try:
                obs=read_observations(trace.client,config,r['trace_id'])
                scores=verify_scores(r['score_export']['scores'],read_scores(config,r['trace_id']))
                readback.append(dict(trace_id=r['trace_id'],observations=obs,scores=scores))
            except Exception as exc:readback.append(dict(trace_id=r['trace_id'],error_type=type(exc).__name__))
    write(output/'trace-readback.json',readback)
    models=[r[k] for r in records for k in ['proposal_record','inventory_record']]
    summary=dict(version=VERSION,regions=len(records),focus_targets=sum(len(r['focus_target_ids']) for r in records),
        actual_api_calls=adapter.calls,completed_calls=sum(m['status']=='completed' for m in models),
        replayed_model_outputs=sum(m['status']=='replayed' for m in models),
        estimated_cost_usd=sum(m.get('estimated_cost_usd') or 0 for m in models),
        unknown_cost_calls=sum(m.get('estimated_cost_usd') is None for m in models),
        inputs_unchanged=before==hashes(protected),axes_unchanged=all(r['axes_unchanged'] for r in records),
        trace_warnings=trace.warnings,semantic_verified=False,facts_published=0,
        scope='development period-context only; existing axis errors unchanged; no RAG publication')
    write(output/'summary.json',summary);make_report(output,records,summary)
    print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--allow-external',action='store_true');p.add_argument('--max-calls',type=int,default=4)
    p.add_argument('--langfuse-config',type=Path);p.add_argument('--env-file',type=Path)
    p.add_argument('--replay-run',type=Path)
    args=vars(p.parse_args());env=args.pop('env_file')
    if env and args['allow_external']:
        for line in env.read_text(encoding='utf-8-sig').splitlines():
            if line.strip().startswith('OPENAI_API_KEY='):
                os.environ.setdefault('OPENAI_API_KEY',line.split('=',1)[1].strip().strip('\"\''))
    run(**args)
