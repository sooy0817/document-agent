"""Prepare immutable cases, then explicitly run paid inference (never in pytest).

Gold/reference labels are read ONLY by the reporter, never by graph or provider.
"""
import argparse
import hashlib
import html
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from doc_understanding.domain.pipeline_contracts import CanonicalDocument
from doc_understanding.parser.relations.continuation_graph import discover_pairs, build_continuation_graph
from doc_understanding.parser.relations.continuation_provider import ContinuationModel
from doc_understanding.runtime.telemetry import Trace, load_config


def read(path): return json.loads(path.read_text(encoding='utf8'))
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def write(path,value):
    with path.open('x',encoding='utf8') as f: json.dump(value,f,ensure_ascii=False,indent=2)


def prepare(job, selection, output):
    output.mkdir(parents=True,exist_ok=False)
    doc=CanonicalDocument.model_validate_json((job/'document.json').read_bytes())
    pairs=discover_pairs(doc)
    cases=[]
    for c in read(selection):
        matching=[p for p in pairs if p['source_page']==c['source_page'] and p['target_page']==c['target_page']
                  and p['source_table_id'].endswith('/'+c['source_fragment'])
                  and p['target_table_id'].endswith('/'+c['target_fragment'])]
        if len(matching)!=1: raise ValueError('PAIR_NOT_UNIQUELY_DISCOVERED:'+c['id'])
        cases.append(dict(id=c['id'],pair=matching[0]))
    write(output/'cases.json',cases)
    write(output/'reference.json',dict(status='source_reviewed_draft_not_human_approved',
        split='development',cases=read(selection)))
    write(output/'candidate-inventory.json',pairs)
    sources={str(job/f):sha(job/f) for f in ('input.pdf','document.json','nested.json','result.json')}
    code_paths=[ROOT/'src/doc_understanding/parser'/f for f in
                ('continuation_graph.py','continuation_provider.py','logical_tables.py')]+[Path(__file__)]
    (output/'code-frozen').mkdir()
    for p in code_paths: shutil.copy2(p,output/'code-frozen'/p.name)
    write(output/'manifest.json',dict(job=str(job),source_hashes=sources,
        code_hashes={str(p):sha(p) for p in code_paths},cases_sha256=sha(output/'cases.json'),
        reference_sha256=sha(output/'reference.json'),candidate_inventory_count=len(pairs),
        notice='References excluded from model/graph input. No independent holdout claim.'))
    print(json.dumps(dict(prepared=str(output),cases=len(cases),inventory=len(pairs))))


def run(prepared, output, allow_external, max_calls, langfuse_config):
    if not allow_external: raise ValueError('Use --allow-external for paid source-image inference')
    manifest=read(prepared/'manifest.json')
    for name,value in {**manifest['source_hashes'],**manifest['code_hashes']}.items():
        if sha(Path(name))!=value: raise ValueError('FROZEN_INPUT_CHANGED:'+name)
    if sha(prepared/'cases.json')!=manifest['cases_sha256'] or sha(prepared/'reference.json')!=manifest['reference_sha256']:
        raise ValueError('FROZEN_CASES_CHANGED')
    output.mkdir(parents=True,exist_ok=False)
    os.environ['LANGSMITH_TRACING']='false';os.environ['LANGCHAIN_TRACING_V2']='false'
    job=Path(manifest['job'])
    doc=CanonicalDocument.model_validate_json((job/'document.json').read_bytes())
    model=ContinuationModel(allow_external=True,max_calls=max_calls)
    trace=Trace(load_config(langfuse_config) if langfuse_config else None)
    import pymupdf
    with pymupdf.open(job/'input.pdf') as pdf:
        def images(packet):
            result={}
            for page in packet['pages']:
                path=output/f'page-{page}.png'
                if not path.exists():
                    pdf[page-1].get_pixmap(matrix=pymupdf.Matrix(1.6,1.6)).save(path)
                result[page]=path.read_bytes()
            return result
        records=[]
        for case in read(prepared/'cases.json'):
            tid=url=None
            with trace.span('table-continuation-evidence',dict(run_id=output.name,mode='source_grounded_continuation',
                    source_cell_ids=[case['pair']['source_table_id'],case['pair']['target_table_id']])) as box:
                if trace.client:
                    try:
                        tid=trace.client.get_current_trace_id()
                        url=trace.client.get_trace_url(trace_id=tid)
                    except Exception: pass
                state=build_continuation_graph(doc,case['pair'],image_loader=images,model=model,trace=trace).invoke({})
                box['output']=dict(result=state['final'],status=state['final']['decision'])
            record=dict(id=case['id'],trace_id=tid,trace_url=url,state=state)
            write(output/(case['id']+'.json'),record)
            records.append(record)
            print(json.dumps(dict(id=case['id'],rule_linked=state['rule_baseline']['linked'],
                proposal=state.get('proposal',{}).get('decision'),final=state['final']['decision'],
                errors=state['final']['reasons'],calls=state['model_calls']),ensure_ascii=False),flush=True)
    trace.flush()
    refs={c['id']:c for c in read(prepared/'reference.json')['cases']}
    comparison=[]
    for r in records:
        s=r['state'];gold=refs[r['id']]['expected']
        comparison.append(dict(id=r['id'],reference=gold,rule_linked=s['rule_baseline']['linked'],
            model_proposal=s.get('proposal',{}).get('decision'),final=s['final']['decision'],
            rule_relation_match=s['rule_baseline']['linked']==(gold=='link_candidate'),
            final_relation_match=s['final']['decision']==gold))
    calls=[c for r in records for c in r['state'].get('model_records',[])]
    summary=dict(comparisons=comparison,api_calls=model.calls,
        estimated_cost_usd=sum(c.get('estimated_cost_usd') or 0 for c in calls),
        calls_with_unknown_cost=sum(c.get('attempts',0)>0 and c.get('estimated_cost_usd') is None for c in calls),
        rule_relation_matches=sum(c['rule_relation_match'] for c in comparison),
        graph_relation_matches=sum(c['final_relation_match'] for c in comparison),total=len(comparison),
        known_positive_count=sum(c['reference']=='link_candidate' for c in comparison),
        known_negative_count=sum(c['reference']=='separate' for c in comparison),
        final_false_links=sum(c['reference']=='separate' and c['final']=='link_candidate' for c in comparison),
        final_abstentions=sum(c['final']=='insufficient_evidence' for c in comparison),
        sources_unchanged=all(sha(Path(p))==v for p,v in manifest['source_hashes'].items()),
        trace_warnings=trace.warnings,semantic_verified=False,
        notice='Small source-reviewed development comparison. Pair relation match is NOT cell-path/scope accuracy. Rule non-link means no candidate, not an explicit separate judgment.')
    write(output/'summary.json',summary)
    render_report(output,records,refs,summary,job)
    print(json.dumps(summary,ensure_ascii=False),flush=True)


def render_report(output,records,refs,summary,job):
    esc=lambda x:html.escape(str(x))
    dump=lambda x:esc(json.dumps(x,ensure_ascii=False,indent=2))
    titles={'link_candidate':'연결 후보','separate':'별개 표','insufficient_evidence':'근거 부족 · 보류'}
    parts=['<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>계속표 관계 · 원본과 판단 근거</title><style>',
        'body{font:15px/1.65 system-ui,"Malgun Gothic";margin:0;background:#faf9f6;color:#24323e}main{max-width:1500px;margin:auto;padding:28px}section{background:white;border:1px solid #d8ddd8;padding:22px;margin:24px 0}a{color:#166c5a}nav{display:flex;gap:15px;flex-wrap:wrap}button{font:inherit;text-align:left;cursor:pointer;border:1px solid #ccd7ce;background:#f3f7f2;padding:8px;margin:4px 0}button:focus-visible{outline:3px solid #166c5a}.images{display:grid;grid-template-columns:1fr 1fr;gap:16px}.sheet{position:relative}.sheet img{width:100%;display:block}.sheet svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}.sheet rect{fill:#ffbb0026;stroke:#b77c00;stroke-width:1.5}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:500px;overflow:auto;font:12px/1.5 Consolas,monospace;background:#f4f5f1;padding:12px}.warning{padding:15px;background:#fff1d6}table{border-collapse:collapse;width:100%}td,th{padding:8px;border:1px solid #d8ddd8;text-align:left;vertical-align:top}.reason{background:#eef4ef;padding:14px}summary{cursor:pointer}h2{scroll-margin-top:20px}@media(max-width:850px){.images{grid-template-columns:1fr}}',
        '</style><main><h1>계속표 관계: 규칙과 원본 이미지 판단 비교</h1><p>원본 셀과 JSON은 그대로입니다. 이 화면은 새 관계 제안·검사·검토 기록을 보여줍니다.</p>',
        '<p class="warning">연결 후보는 사람 승인된 정답이 아닙니다. 표 연결 여부와 헤더·행 적용 범위의 정확도를 구분해서 검토하세요. 모델이 잘못 판단한 결과도 숨기지 않습니다.</p>',
        '<nav>'+''.join(f'<a href="#{esc(r["id"])}">{esc(r["id"])} {r["state"]["pair"]["source_page"]}→{r["state"]["pair"]["target_page"]}쪽</a>' for r in records)+'</nav>',
        '<details><summary>전체 비교 수치 · 사용량 · 제한사항</summary><pre>'+dump(summary)+'</pre></details>']
    import pymupdf
    with pymupdf.open(job/'input.pdf') as pdf:
        for r in records:
            s=r['state'];p=s['pair'];d=s.get('proposal',{});rid=r['id']
            parts.append(f'<section id="{esc(rid)}"><h2>{esc(rid)} · {p["source_page"]}쪽 → {p["target_page"]}쪽</h2>'
                f'<p>규칙: {"연결 후보" if s["rule_baseline"]["linked"] else "후보 없음"} · 모델 제안: {esc(titles.get(d.get("decision"),"실행 안 됨"))} · 최종: <b>{esc(titles[s["final"]["decision"]])}</b></p>'
                f'<p>원본 검토 초안: {esc(titles[refs[rid]["expected"]])} / {esc(refs[rid]["reason"])} (사람 승인 전)</p>'
                f'<p><a href="{esc(rid)}.json">실제 State·요청·응답 JSON</a> · <a href="http://127.0.0.1:8766/?job={job.name}&page={p["target_page"]}">기존 원본/셀/JSON 화면</a>'
                +(f' · <a href="{esc(r["trace_url"])}">Langfuse Trace</a>' if r.get('trace_url') else '')+'</p>')
            parts.append('<div class="images">')
            for page in dict.fromkeys([p['source_page'],p['target_page']]):
                rect=pdf[page-1].rect
                parts.append(f'<div><b>원본 {page}쪽</b><div class="sheet" data-page="{page}"><a href="page-{page}.png" target="_blank"><img loading="lazy" src="page-{page}.png" alt="원본 {page}쪽"></a><svg viewBox="0 0 {rect.width} {rect.height}"></svg></div></div>')
            parts.append('</div><p class="reason">'+esc(d.get('reason','판단 미실행'))+'</p>')
            for key,title in [('supporting_evidence','지지 근거'),('contradicting_evidence','반대 근거')]:
                parts.append('<h3>'+title+'</h3>')
                for e in d.get(key,[]):
                    c=s['evidence']['registry'].get(e['source_id'],{})
                    payload=esc(json.dumps([c],ensure_ascii=False))
                    parts.append(f'<button data-focus="{payload}">p.{c.get("page","?")} · {esc(e["quote"])}<br>{esc(e["reason"])}</button>')
                if not d.get(key): parts.append('<p>기록 없음</p>')
            parts.append('<h3>제안된 적용 범위 · 원문 셀</h3>')
            for scope in d.get('scopes',[]):
                registry=s['evidence']['registry'];source=[registry.get(i,{'id':i}) for i in scope['source_cell_ids']]
                target=[registry.get(i,{'id':i}) for i in scope['target_cell_ids']]
                parts.append('<details><summary>'+esc(scope['kind'])+' · '+esc(' / '.join(c.get('text','?') for c in source))+f' → {len(target)}셀</summary>'
                    '<p>'+esc(scope['reason'])+'</p><button data-focus="'+esc(json.dumps(source+target,ensure_ascii=False))+'">원본에서 적용 범위 표시</button>'
                    '<pre>'+dump(dict(source=source,target=target))+'</pre></details>')
            parts.append('<h3>검사·의미 검토</h3><pre>'+dump(dict(validation=s.get('validation'),review=s.get('review'),final=s['final']))+'</pre>'
                '<details><summary>LangGraph 단계별 기록</summary><pre>'+dump(s['steps'])+'</pre></details></section>')
    parts.append('''<script>document.querySelectorAll('[data-focus]').forEach(b=>b.onclick=()=>{const section=b.closest('section');section.querySelectorAll('svg').forEach(v=>v.replaceChildren());for(const c of JSON.parse(b.dataset.focus)){const svg=section.querySelector('.sheet[data-page="'+c.page+'"] svg');if(!svg||!c.bbox)continue;const r=document.createElementNS('http://www.w3.org/2000/svg','rect');r.setAttribute('x',c.bbox[0]);r.setAttribute('y',c.bbox[1]);r.setAttribute('width',c.bbox[2]-c.bbox[0]);r.setAttribute('height',c.bbox[3]-c.bbox[1]);svg.append(r);}section.querySelector('.images').scrollIntoView({block:'start',behavior:'smooth'});});</script></main></html>''')
    (output/'comparison.html').write_text(''.join(parts),encoding='utf8')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--job',type=Path);parser.add_argument('--selection',type=Path)
    parser.add_argument('--prepared',type=Path);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--allow-external',action='store_true');parser.add_argument('--max-calls',type=int,default=24)
    parser.add_argument('--langfuse-config',type=Path)
    a=parser.parse_args()
    if a.prepared: run(a.prepared.resolve(),a.output.resolve(),a.allow_external,a.max_calls,a.langfuse_config)
    elif a.job and a.selection: prepare(a.job.resolve(),a.selection,a.output.resolve())
    else: parser.error('Choose --job + --selection (offline) or --prepared (explicit live)')
