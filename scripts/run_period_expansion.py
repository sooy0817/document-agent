"""Immutable prepare/live experiment; no parser changes, no operational DB writes."""
import argparse
import hashlib
import html
import json
import os
from pathlib import Path
from run_axis_judge import ROOT,read,write,hashes
from doc_understanding.domain.models import DocumentIR
from doc_understanding.parser.physical.physical_models import PhysicalTableIR
from doc_understanding.agents.axis_review.axis_resolver import AxisRegion, regions_for
from doc_understanding.parser.context.period_decisions import DecisionAdapter
from doc_understanding.parser.context.period_coverage import build_coverage_graph
from doc_understanding.parser.context.period_expansion import expand_context, ExpansionAdapter, build_expansion_graph
from doc_understanding.runtime.telemetry import Trace,load_config
from doc_understanding.rag.observability.judge import publish_scores


def inventory(document_path):
    doc=DocumentIR.model_validate(read(document_path));run=document_path.parent
    paths=[p for p in sorted((run/'physical').glob('page-*-table-*.json')) if not p.stem.endswith('-structured')]
    physical=[PhysicalTableIR.model_validate(read(p)) for p in paths]
    # Empty reconstructions have no context cells to retrieve. Preserve their
    # files/hashes and list them in preparation diagnostics, not as successes.
    return doc,[r for p in doc.pages for r in regions_for(p,[t for t in physical if t.cells])],paths


def render_packet(packet,pdf_path):
    import pymupdf
    region=AxisRegion.model_validate(packet['expanded_region']);panels=[]
    with pymupdf.open(pdf_path) as pdf:
        for page in sorted(set(packet['cell_pages'].values())):
            boxes=[c.bbox for c in region.cells if packet['cell_pages'][c.id]==page]
            clip=pymupdf.Rect(min(b.x0 for b in boxes)-5,min(b.y0 for b in boxes)-5,
                             max(b.x1 for b in boxes)+5,max(b.y1 for b in boxes)+5)&pdf[page-1].rect
            panels.append(dict(page=page,bbox=list(clip),scale=2))
        with pymupdf.open() as sheet:
            width=max(p['bbox'][2]-p['bbox'][0] for p in panels)
            height=sum(p['bbox'][3]-p['bbox'][1]+20 for p in panels)
            canvas=sheet.new_page(width=width,height=height);y=0
            for panel in panels:
                clip=pymupdf.Rect(panel['bbox'])
                canvas.insert_text((5,y+12),f"Original PDF page {panel['page']} | page-local coordinates",fontsize=9)
                canvas.show_pdf_page(pymupdf.Rect(0,y+20,clip.width,y+20+clip.height),pdf,panel['page']-1,clip=clip)
                y+=clip.height+20
            image=canvas.get_pixmap(matrix=pymupdf.Matrix(2,2),alpha=False).tobytes('png')
    return image,panels


def code_paths():
    return [ROOT/'src/doc_understanding/parser'/name for name in
            ['period_expansion.py','period_coverage.py','period_decisions.py','period_context.py']]+[Path(__file__)]


def prepare(manifest,output):
    output.mkdir(parents=True,exist_ok=False);loaded=[];protected=[manifest]
    for n,c in enumerate(read(manifest)['cases']):
        saved=read(ROOT/c['saved']);case=saved['case'];source=ROOT/case['source']
        region=AxisRegion.model_validate(read(source));docpath=ROOT/c['document_ir']
        doc,regions,paths=inventory(docpath);targets=case['target_ids']
        write(output/f'case-{n:03d}-inventory.json',dict(total_physical_tables=len(paths),
            empty_physical_tables=[dict(path=str(p),table_id=read(p)['table_id']) for p in paths if not read(p)['cells']],
            reason='Empty original tables preserved; unavailable as context sources, not successful reconstructions'))
        packet=expand_context(region,targets,doc,regions)
        write(output/f'case-{n:03d}-packet.json',packet)
        image,panels=render_packet(packet,docpath.parent/'source.pdf')
        (output/f'case-{n:03d}-expanded.png').write_bytes(image)
        loaded.append(dict(**c,source=case['source'],original_image=case['image'],target_ids=targets,
            packet=f'case-{n:03d}-packet.json',expanded_image=f'case-{n:03d}-expanded.png',panels=panels))
        protected += paths+[source,ROOT/c['saved'],ROOT/case['image'],docpath,docpath.parent/'source.pdf']
        print(json.dumps(dict(case=c['id'],status=packet['status'],added=len(packet['additions']),
            missing_before=[len(t['missing_span_ids']) for t in packet['before']['targets']],
            missing_after=[len(t['missing_span_ids']) for t in packet['after']['targets']]),ensure_ascii=False),flush=True)
    for dirname in ['period-context-20261030','period-decisions-20261030','period-coverage-20261030']:
        protected+=list((ROOT/'reports'/dirname).rglob('*.json'))
    write(output/'cases.json',dict(cases=loaded))
    write(output/'freeze.json',dict(inputs=hashes(protected),code=hashes(code_paths())))


def live(prepared,output,allow_external,max_calls,langfuse_config,env_file):
    if not allow_external:raise ValueError('EXPLICIT_LIVE_FLAG_REQUIRED')
    frozen=read(prepared/'freeze.json')
    if any(hashlib.sha256(Path(p).read_bytes()).hexdigest()!=h for p,h in {**frozen['inputs'],**frozen['code']}.items()):
        raise ValueError('FROZEN_INPUT_OR_CODE_CHANGED')
    cases=read(prepared/'cases.json')['cases']
    needed=sum(2+(2 if read(prepared/c['packet'])['status']=='expanded' else 0) for c in cases)
    if max_calls<needed:raise ValueError('CALL_BUDGET_TOO_SMALL')
    if env_file:
        for line in env_file.read_text(encoding='utf-8-sig').splitlines():
            if line.strip().startswith('OPENAI_API_KEY='):
                os.environ.setdefault('OPENAI_API_KEY',line.split('=',1)[1].strip().strip('\"\''))
    output.mkdir(parents=True,exist_ok=False)
    config=load_config(langfuse_config) if langfuse_config else None;trace=Trace(config)
    records=[];ledger=[]
    for n,c in enumerate(cases):
        print(json.dumps(dict(case=c['id'],phase='start')),flush=True)
        region=AxisRegion.model_validate(read(ROOT/c['source']));doc,regions,_=inventory(ROOT/c['document_ir'])
        tid=oid=None
        with trace.span('period-context-expansion-experiment',dict(run_id=output.name,source_cell_ids=c['target_ids'],mode='fixed_prompt_paired_source_inputs')) as box:
            if trace.client:
                try:tid=trace.client.get_current_trace_id();oid=trace.client.get_current_observation_id()
                except Exception:pass
            with trace.span('original_input_fresh_control'):
                original=build_coverage_graph(region,c['target_ids'],(ROOT/c['original_image']).read_bytes(),
                    DecisionAdapter(allow_external=True,max_calls=2),doc,trace).invoke({})
            graph=build_expansion_graph(region,c['target_ids'],doc,regions,
                lambda packet:ExpansionAdapter(packet,allow_external=True,max_calls=2),
                lambda packet:(prepared/c['expanded_image']).read_bytes(),trace)
            if n==0:(output/'graph.mmd').write_text(graph.get_graph().draw_mermaid(),encoding='utf-8')
            expansion=graph.invoke({})
            if expansion['packet']!=read(prepared/c['packet']):raise ValueError('PREPARED_PACKET_CHANGED')
            final=expansion.get('result',original);specs=[]
            for phase,value in [('original',original),('final',final)]:
                for i,d in enumerate((value.get('guarded_proposal') or {}).get('decisions',[])):
                    specs.append(dict(name=f'expansion.{phase}.t{i}',value=d['status'],data_type='CATEGORICAL',
                                      comment='target='+d['target_cell_id']+' | '+d['reason']))
            exported=publish_scores(trace,config,specs,tid,oid)
            box['output']=dict(status=expansion['status'],retry_count=expansion['retry_count'],
                              semantic_status='candidate_not_verified',facts_published=0)
        record=dict(case=c,original=original,expansion=expansion,final=final,trace_id=tid,score_export=exported)
        if trace.client and tid:
            try:record['trace_url']=trace.client.get_trace_url(trace_id=tid)
            except Exception:pass
        write(output/f'case-{n:03d}.json',record);records.append(record)
        for state in [original]+([expansion['result']] if 'result' in expansion else []):
            ledger.extend(state['raw'][key] for key in ['proposal_record','inventory_record'])
        trace.flush()
        print(json.dumps(dict(case=c['id'],retry=expansion['retry_count'],
            final=[(d['target_cell_id'].split('/')[-1],d['status']) for d in (final['guarded_proposal'] or {}).get('decisions',[])]),ensure_ascii=False),flush=True)
    summary=dict(cases=len(records),targets=sum(len(c['target_ids']) for c in cases),actual_api_attempts=len(ledger),
        completed_calls=sum(r['status']=='completed' for r in ledger),
        estimated_cost_usd=sum(r.get('estimated_cost_usd') or 0 for r in ledger),
        unknown_cost_calls=sum(r.get('estimated_cost_usd') is None for r in ledger),
        inputs_unchanged=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in frozen['inputs'].items()),
        prompt_unchanged=all(r['prompt']==DecisionAdapter.prompts[r['stage']] for r in ledger),
        trace_warnings=trace.warnings,semantic_verified=False,facts_published=0,
        evaluation_scope='development; not independent generalization or full row/column accuracy')
    write(output/'summary.json',summary);render_report(prepared,output,records,summary)
    print(json.dumps(summary,ensure_ascii=False),flush=True)


def render_report(prepared,output,records,summary):
    esc=lambda x:html.escape(str(x));blocks=[]
    for n,r in enumerate(records):
        c=r['case'];packet=r['expansion']['packet'];rows=[]
        saved=read(ROOT/c['saved'])
        phases=[('이전 저장 응답',saved['proposal']),('기존 입력 새 실행',r['original']['guarded_proposal']),('최종',r['final']['guarded_proposal'])]
        for phase,result in phases:
            for d in (result or {}).get('decisions',[]):
                rows.append('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in [phase,d['target_cell_id'].split('/')[-2:],d['status'],d['reason'],d['missing_evidence']])+'</tr>')
        blocks.append(f'<h2>{esc(c["id"])}</h2><p>문맥 추가 {len(packet["additions"])}셀 · 재판단 {r["expansion"]["retry_count"]}회</p>'
            f'<p><a href="case-{n:03d}.json">전체 실행 JSON</a> · <a href="{esc((prepared/c["packet"]).resolve().as_uri())}">원본/확장 셀 JSON</a> · '
            f'<a href="{esc(r.get("trace_url", "#"))}">Langfuse</a></p>'
            f'<div class="pair"><figure><figcaption>기존 입력 이미지</figcaption><img src="{esc((ROOT/c["original_image"]).as_uri())}"></figure>'
            f'<figure><figcaption>확장 입력 원본 crop · 후보 문맥, 의미 승인 아님</figcaption><img src="{esc((prepared/c["expanded_image"]).resolve().as_uri())}"></figure></div>'
            '<table><tr><th>조건</th><th>셀</th><th>판단</th><th>근거</th><th>보류 사유</th></tr>'+''.join(rows)+'</table>'
            '<details><summary>추가된 원본 셀과 문맥 검사</summary><pre>'+esc(json.dumps(dict(additions=packet['additions'],after=packet['after']),ensure_ascii=False,indent=2))+'</pre></details>')
    (output/'comparison.html').write_text('<!doctype html><meta charset="utf-8"><title>원문 문맥 확장과 1회 재판단</title>'
        '<style>body{font:16px/1.7 system-ui;max-width:1400px;margin:30px auto;padding:20px}.pair{display:flex;align-items:start}.pair figure{width:50%;margin:10px}img{max-width:100%}table{border-collapse:collapse;width:100%}td,th{border:1px solid #bbb;padding:8px;vertical-align:top}pre{white-space:pre-wrap;overflow-wrap:anywhere}h2{margin-top:50px}</style>'
        '<h1>부모·계속표 문맥 수집 → 최대 1회 재판단</h1><p>기존 원문·IR·프롬프트 보존. 생성기와 독립 Judge는 같은 원본 근거를 보며 상대 응답이나 정답 참조는 받지 않습니다. 미검증 후보이며 DB/RAG·row/col 경로는 변경하지 않았습니다.</p>'
        '<p>같은 개발 사례의 작은 비교입니다. 입력 확대에는 원문 이미지 범위와 페이지 표기도 포함됩니다. 샘플링 변동을 완전히 분리하지 못하며, 모델 일치율은 독립 정답 정확도가 아닙니다. 추가가 없는 문서는 새 기존 입력 실행만 하고 불필요한 재판단을 생략합니다.</p>'
        '<pre>'+esc(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre>'+''.join(blocks),encoding='utf-8')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path);p.add_argument('--prepared',type=Path)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--allow-external',action='store_true')
    p.add_argument('--max-calls',type=int,default=10);p.add_argument('--langfuse-config',type=Path);p.add_argument('--env-file',type=Path)
    a=p.parse_args()
    if a.manifest:prepare(a.manifest,a.output)
    elif a.prepared:live(a.prepared,a.output,a.allow_external,a.max_calls,a.langfuse_config,a.env_file)
    else:p.error('Choose --manifest for offline prepare or --prepared for explicit live execution')
