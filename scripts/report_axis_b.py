"""Render immutable source/candidate/A-B results, never modifies evaluated code/data."""
import json,html,hashlib,re
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'reports/axis-judge-b-20261029'
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def esc(v):return html.escape(str(v))
def link(p,label):return f'<a href="{esc(Path(p).resolve().as_uri())}">{esc(label)}</a>'
def norm(s):return re.sub(r'\s+','',s)
def evaluate(c,folder):
 r=read(ROOT/c['source']);cells={x['id']:x for x in r['cells']};g=read(ROOT/c['generated'])['generated'];p=next(x for x in g['links'] if x['cell_id']==c['target_id'])
 row=[cells[x]['text'] for x in p['row_path']];col=[cells[x]['text'] for x in p['column_path']]
 known=c.get('expected_row') is not None and c.get('expected_col') is not None
 exact=known and [norm(x) for x in row]==[norm(x) for x in c['expected_row']] and [norm(x) for x in col]==[norm(x) for x in c['expected_col']]
 result=dict(id=c['id'],split=c.get('split','development'),row=row,column=col,reference_known=known,exact_paths=exact if known else None,
  expected_verdict='unknown' if not known else 'correct' if exact else 'incorrect')
 cards=[]
 for variant in ['A','B']:
  f=folder/(c['id']+'-'+variant+'.json');d=read(f);j=d.get('judgement') or {};v=d['validation'];scores={x['dimension']:x['score'] for x in j.get('grades',[])}
  result[variant]=dict(verdict=j.get('verdict'),valid=v['valid'],errors=v['errors'],scores=scores,
   reference_scope='paths_only_not_overall_role_or_scope_gold',trace_id=d.get('trace_id'))
  body=f'<h3>{variant} · {esc(j.get("verdict"))} · 검증 {v["valid"]}</h3><p>{esc(scores)}</p><p class="bad">{esc(v["errors"])}</p>'
  if variant=='B':
   axes=[]
   for a in j.get('axes',[]):
    labels=lambda key:[cells.get(x,{}).get('text',x) for x in a[key]]
    axes.append(dict(axis=a['axis'],existence=a['existence'],direct=labels('direct_header_ids'),ancestor=labels('ancestor_header_ids'),missing=labels('missing_header_ids'),extra=labels('extraneous_header_ids'),reason=a['reason']))
   result['B']['inventories']=axes;body+='<pre>'+esc(json.dumps(axes,ensure_ascii=False,indent=2))+'</pre>'
  body+='<details><summary>rubric 근거 전체</summary><pre>'+esc(json.dumps(j.get('grades'),ensure_ascii=False,indent=2))+'</pre></details>'
  body+=link(f,'실제 Judge JSON')+' · '+f'<a href="{esc(d.get("trace_url","#"))}">Langfuse Trace</a>'
  cards.append('<div>'+body+'</div>')
 section=f'<section><h2>{esc(c["id"])} · {esc(c["label"])}</h2><p>구분: {esc(result["split"])} / 참조 판정: {result["expected_verdict"]} (독립 사람 Gold 아님)</p><div class="pair"><div><img src="{(ROOT/c["image"]).resolve().as_uri()}"></div><div><h3>고정 후보</h3><p>원문 값: {esc(cells[c["target_id"]]["text"])}</p><p>행: {esc(row)}</p><p>열: {esc(col)}</p><p>원본 참조 행: {esc(c.get("expected_row"))}</p><p>원본 참조 열: {esc(c.get("expected_col"))}</p><p>값 ID: {esc(c["target_id"])}</p>'+link(ROOT/c['source'],'원본 셀 JSON')+' · '+link(ROOT/c['generated'],'고정 생성 후보 JSON')+'</div></div><div class="pair">'+''.join(cards)+'</div></section>'
 return result,section
if __name__=='__main__':
 rows=[];sections=[]
 for cases,folder in [(read(ROOT/'reports/axis-judge-20261029/cases.json')['cases'],BASE/'development-01'),(read(BASE/'holdout-cases.json')['cases'],BASE/'holdout-01')]:
  for c in cases:
   result,section=evaluate(c,folder);rows.append(result);sections.append(section)
 frozen=read(BASE/'implementation-freeze.json');unchanged=all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in frozen.items())
 summaries=[read(BASE/d/'summary.json') for d in ['development-01','holdout-01']]
 generation=[read(p) for p in (BASE/'holdout').glob('*-generation/manifest.json')]
 totals=dict(judge_calls=sum(s['actual_api_calls'] for s in summaries),generation_calls=sum(s['actual_api_attempts'] for s in generation),
  judge_cost_usd=sum(s['estimated_cost_usd'] for s in summaries),generation_cost_usd=sum(s['estimated_cost_usd'] for s in generation),
  frozen_code_unchanged=unchanged,original_inputs_unchanged=all(s['inputs_unchanged'] for s in summaries),tests_passed=51,
  new_documents=4,additional_regression_documents=1,new_selected_targets=8,new_paired_targets=6,new_unavailable_targets=2,
  human_gold=False,facts_published=0)
 data=dict(totals=totals,cases=rows,unavailable=read(BASE/'holdout-cases.json')['unavailable'])
 with (BASE/'evaluation.json').open('x',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,indent=2)
 docrows=''
 for d in read(ROOT/'data/axis-judge-b-holdout-20261029/acquisition.json'):
  docrows+=f'<tr><td>{d["id"]}</td><td>{esc(d["title"])}</td><td>{"기존 회귀" if d["id"]=="H02" else "신규"}</td><td>{d["pages"]}</td><td>'+link(ROOT/d['path'],'보존 PDF')+f' · <a href="{esc(d["url"])}">공개 출처</a></td></tr>'
 intro='''<h1>B Judge: 직접 제목·상위 제목·누락을 명시한 A/B 평가</h1>
 <p>기존 파서·생성기·A Judge·IR·Canonical·DB를 변경하지 않았다. 동일 후보·동일 이미지·동일 GPT-4.1 snapshot·출력 예산 4000으로 비교했다. 정답 참조는 모델 입력에서 제외했다. 새 문서 확인 전 구현을 동결했다.</p>
 <p><b>개발 사례:</b> A는 iM 4건을 열 오류로 incorrect라 했지만 행 경로에는 모두 만점을 줬다. B는 4건 모두 중도해지수수료율 상위를 찾았으나, 누락 목록과 점수의 불일치로 4건 모두 검증 보류됐다. 2건은 행 필드 제목까지 상위 경로에 추가했다. 하나은행 정상 후보는 A/B 모두 correct를 유지했다. 따라서 오류 통과 방지와 Judge 자체의 정확성을 구별해야 한다.</p>
 <p><b>평가 한계:</b> 신규 4문서의 사전 선정 8대상 중 6대상만 A/B까지 도달했다. 2대상은 실패로 분모에 보존했다. NH저축은행은 과거 동일 PDF 해시가 있어 신규가 아닌 회귀로 분류했다. 원본을 보고 작성한 참조는 AI 보조 검토이며 독립 사람 Gold가 아니다. 참조 판정은 경로에 관한 것으로 역할·범위를 포함한 Judge 전체 verdict와 같지 않다. 평가 단위는 선택한 셀·영역이며 PDF 전체 정확도를 의미하지 않는다.</p>
 <p><b>새 문서에서 드러난 실패:</b> KB 계속표(new-004)는 이전 페이지 제목이 입력에 없는데 A/B 모두 만점을 줬고 B는 unknown을 absent로 해석했다. 구미송정 배점표(new-006)는 같은 헤더 행의 왼쪽 머리칸 '항목'을 '3점'의 상위 열로 오인했으며 A/B 모두 통과시켰다. 카카오 금리(new-008)는 B가 빠진 기간 라벨을 찾아냈으나 열 헤더 없음의 rubric 근거 ID가 비어 출력 검증에서 보류됐다. 구미송정 1순위(new-005)는 두 방식 모두 경로는 만점이지만 B가 '1순위'의 value 겸용 역할을 문제 삼았다. 이는 경로 오답과 역할 정책 차이를 구분해서 볼 사례다.</p>
 <p><b>도식·양식:</b> 문서 전체가 아닌 정보 표의 선택 대상만 평가했다. KB 계속표의 같은 큰 영역에 계산 설명 그림이 있으나 그림의 노드/화살표는 평가 대상이 아니다. 현재 입력 crop에는 이 주변부가 남아 있으므로 도식이 전혀 없는 PDF 집합이라는 주장도 하지 않는다.</p>
 <p><b>흐름:</b> 원본 영역·고정 후보 → prepare_candidate → judge_llm(A 또는 B) → validate_judgement → 검토용 sidecar. 의미 확정·자동 수정·사실 발행·학습 없음.</p>'''
 failures='<section><h2>Judge 이전 실패 — 제외하지 않음</h2><p>H01 p3: INSUFFICIENT_PHYSICAL_EVIDENCE. 원문은 있지만 사용 가능한 물리 셀이 부족해 호출 0회. H05 p5: regions_for에서 빈 bbox 목록에 min()을 적용하여 ValueError. 이 작업에서는 동결된 파서를 수정하지 않았다.</p><pre>'+esc(json.dumps(data['unavailable'],ensure_ascii=False,indent=2))+'</pre></section>'
 table='<table><tr><th>자료</th><th>문서</th><th>분리</th><th>전체 페이지</th><th>출처</th></tr>'+docrows+'</table>'
 summarytable='<table><tr><th>사례</th><th>분리</th><th>경로 참조</th><th>A 전체 판정</th><th>B 전체 판정</th><th>B 출력 검증</th></tr>'+''.join(f'<tr><td>{r["id"]}</td><td>{r["split"]}</td><td>{r["expected_verdict"]}</td><td>{r["A"]["verdict"]}</td><td>{r["B"]["verdict"]}</td><td>{r["B"]["valid"]}: {esc(r["B"]["errors"])}</td></tr>' for r in rows)+'</table>'
 css='body{font:16px/1.6 system-ui;margin:32px;color:#182637;background:#f4f6f8}section{background:white;padding:24px;margin:24px 0;border:1px solid #cbd5df}.pair{display:grid;grid-template-columns:1fr 1fr;gap:24px}img{max-width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f4f6;padding:12px;font-size:13px}p{overflow-wrap:anywhere}td,th{padding:10px;border:1px solid #cbd5df;text-align:left}table{border-collapse:collapse;width:100%;background:white}.bad{color:#973820}a{color:#0756a0}'
 text='<!doctype html><meta charset="utf-8"><title>B Judge A/B 검증</title><style>'+css+'</style>'+intro+table+'<h2>실행 요약</h2><pre>'+esc(json.dumps(totals,ensure_ascii=False,indent=2))+'</pre>'+summarytable+failures+''.join(sections)
 (BASE/'comparison.html').write_text(text,encoding='utf-8')
 print(json.dumps(totals,ensure_ascii=False))
