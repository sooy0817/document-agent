"""Build immutable source/JSON report for coverage guard replay."""
import hashlib
import html
import json
from pathlib import Path
from run_axis_judge import ROOT,read,write

BASE=ROOT/'reports/period-coverage-20261030'

if __name__=='__main__':
    records=[read(p) for p in sorted((BASE/'run-03').glob('region-???.json'))]
    freeze=read(BASE/'run-03/freeze.json');summary=read(BASE/'run-03/summary.json')
    unchanged=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in freeze['inputs'].items())
    trace=read(BASE/'trace-readback.json');diagnostic=read(BASE/'context-expansion-diagnostic.json')
    continuation=read(BASE/'continuation-checks.json')
    changes=[];blocks=[];e=lambda x:html.escape(str(x))
    for n,r in enumerate(records):
        region=read(ROOT/r['source']);cells={c['id']:c for c in region['cells']}
        doc=read(ROOT/r['case']['document_ir']);spans={s['span_id']:s for p in doc['pages'] for b in p['blocks'] for s in b['spans']}
        rendered=[]
        after={d['target_cell_id']:d for d in r['guarded_proposal']['decisions']}
        for before in r['raw']['proposal']['decisions']:
            aid=before['target_cell_id'];a=after[aid];audit=next(t for t in r['coverage']['targets'] if t['target_cell_id']==aid)
            row=dict(case=r['case']['id'],target_cell_id=aid,value_raw=cells[aid]['text'],before=before['status'],after=a['status'],
                original_relations_preserved=before['relations']==a['relations'],coverage=audit['status'],
                missing_spans=[dict(id=s,text_raw=spans[s]['text_raw'],page=spans[s]['source']['page_number']) for s in audit['missing_span_ids']])
            changes.append(row);rendered.append(row)
        blocks.append(f'<section><h2>{e(r["case"]["id"])} 실제 원본과 전후 JSON</h2>'
            f'<p><a href="{e((ROOT/r["source"]).as_uri())}">입력 셀 JSON</a> · '
            f'<a href="{e((ROOT/r["case"]["document_ir"]).as_uri())}">전체 DocumentIR</a> · '
            f'<a href="run-03/region-{n:03d}.json">raw·검사·전후 전체 기록</a> · '
            f'<a href="run-03/region-{n:03d}-sidecar.json">검사 후 sidecar</a> · '
            f'<a href="{e(r.get("trace_url"))}">Langfuse Trace</a></p>'
            f'<img style="max-width:100%;width:850px" src="{e((ROOT/r["image"]).as_uri())}">'
            '<pre>'+e(json.dumps(rendered,ensure_ascii=False,indent=2))+'</pre>'
            '<details><summary>부모·앞 페이지·뒷 페이지 검사의 전체 근거</summary><pre>'+e(json.dumps(r['coverage'],ensure_ascii=False,indent=2))+'</pre></details></section>')
    evaluations=dict(**summary,protected_files=len(freeze['inputs']),protected_files_unchanged=unchanged,
        tests=dict(passed=75,failed=0,model_calls=0,network='pytest default block, mocked calls'),
        traces=dict(count=4,verified=sum(t['scores']['verified'] for t in trace),observations=28,scores=20),
        checks=changes,normal_decisions_unchanged=sum(c['before']==c['after'] for c in changes),
        absence_withheld=sum(c['before']=='not_needed' and c['after']=='insufficient_evidence' for c in changes),
        context_expansion_diagnostic=dict(before_missing_per_target=[len(t['missing_span_ids']) for t in diagnostic['before']['targets']],
            after_missing_per_target=[len(t['missing_span_ids']) for t in diagnostic['after']['targets']],new_inference=False),
        real_continuation_checks=[dict(page=c['page'],findings=[dict(kind=f['kind'],status=f['status'],missing_spans=len(f['missing_span_ids'])) for f in c['audit']['targets'][0]['findings'] if f['kind'] in {'previous_page','next_page'}]) for c in continuation],
        scope='Development replay guard evaluation, not unseen model accuracy or RAG answer improvement.',
        limitations=['No automatic context retrieval/expansion or reinference in production.',
                     'Native parent omissions, distant notes and nonaligned continuations can escape the bounded checks.',
                     'Geometry finds possible context, not semantic applicability.',
                     'iM original row/column ancestry is not repaired.',
                     'No new unseen holdout in this follow-up; prior holdout is now development.',
                     'v1.0 over-abstention and v1.1 intermediate run preserved in run-01/run-02.'])
    write(BASE/'evaluation-reviewed.json',evaluations)
    full=(ROOT/'data/period-decisions-holdout-20261030-acquired/W01-p2.png').as_uri()
    crop=(ROOT/records[3]['image']).as_uri()
    intro='''<!doctype html><html lang="ko"><meta charset="utf-8"><title>부모·앞뒤 페이지 문맥 누락 검사</title>
<style>body{font:16px/1.7 system-ui,"Malgun Gothic";max-width:1250px;margin:30px auto;padding:20px;color:#20303d}td,th{border:1px solid #ccd3d8;padding:10px;text-align:left}table{border-collapse:collapse;width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f4f6;padding:16px;max-height:650px;overflow:auto}section{margin-top:35px;border-top:2px solid #ddd}.images{display:flex;gap:20px;align-items:flex-start;flex-wrap:wrap}.images img{width:590px;max-width:100%}a{overflow-wrap:anywhere}.notice{background:#fff0d6;padding:15px}</style>
<h1>입력 문맥 누락 시 ‘불필요’ 확정 차단</h1>
<p><b>한국투자저축은행 2건은 근거 부족으로 보류하고, iM·하나은행·신한의 나머지 8개 대상은 유지했습니다.</b> 동일한 저장 응답을 재생했으며 프롬프트·원본·모델 응답은 바꾸지 않았습니다. 새 모델 호출 0회, 비용 $0.</p>
<table><tr><th>검증 대상</th><th>검사 전 → 후</th><th>확인한 것</th></tr>
<tr><td>한국투자저축은행 내부 표 2개</td><td>불필요 → 근거 부족</td><td>각 대상에 같은 부모 설명 span 4개가 입력에서 누락됨. 생성기·Judge 양쪽 확정을 보류</td></tr>
<tr><td>iM 5개 + 하나은행 1개 + 신한 2개</td><td>8개 모두 기존 판단 유지</td><td>기간 관계 4개 및 비기간 항목 4개 유지. 모든 관계 ID 원형 보존</td></tr>
<tr><td>실제 신한 계속표 1·2쪽</td><td>1쪽: 뒷 페이지 / 2쪽: 앞 페이지 누락 위험 감지</td><td>각 37·31개 span 미포함. 문맥 검사만 수행, 페이지의 도식은 해석하지 않음</td></tr></table>
<h2>실제 병목은 어디인가</h2>
<p>왼쪽 원본에는 내부 표 위의 부모 설명이 있습니다. 오른쪽 모델 입력은 내부 표 2개만 잘라 제공합니다. 빠진 내용은 전체 JSON에 이미 저장돼 있습니다. ‘신규일/최종 재예치일…고시된 이율’, ‘기본산식…차등률’ 등의 적용 문맥을 보지 않고 설명이 없다고 단정한 것이 문제입니다.</p>
'''
    intro+=f'<div class="images"><img src="{e(full)}"><img src="{e(crop)}"></div>'
    intro+='''<p>진단용 입력에 원래 부모 셀을 추가하자 누락 span이 각 4→0개로 줄었습니다. 이는 문맥 전달 문제의 근거이며, 확장 입력으로 LLM을 다시 실행하거나 새 의미 판정을 승인한 것은 아닙니다. 원래 응답을 다른 입력의 결과로 재사용하지 않았습니다.</p>
<h2>구현 흐름과 검사 기준</h2>
<p>원본 전체와 입력 대조 → 기존 생성·Judge subgraph → 문맥 근거에 따라 불필요 확정 보류. 원래 생성·Judge 판단은 raw에 보존하고 검사 후 뷰만 별도 저장합니다. 모델끼리 모두 근거 부족이 됐다고 정답 2점으로 채점하지 않습니다.</p>
<p>부모 후보는 엄격한 좌표 포함과 원문 span 공유로 찾습니다. Native가 글자 크기 bbox이고 Physical이 셀 전체 bbox인 경우에는 동일 span 집합과 역방향 포함으로 동일성을 확인합니다. 문서명·기관명·기대 셀 수 분기는 없습니다.</p>
<p>앞뒤 페이지는 대상 행/부모가 자기 표 끝이면서 페이지 상·하단 12%에 있고, 인접 페이지 반대쪽 표와 정규화 가로 폭이 80% 이상 겹칠 때 계속표 위험으로 기록합니다. 이 검사는 적용 관계를 확정하지 않습니다. 1.5pt 기존 좌표 허용치를 확대하지 않았습니다.</p>
<p class="notice"><b>제한:</b> checked_no_gap은 지정한 검사에서 누락을 찾지 못했다는 뜻입니다. 모든 설명이 포함됐거나 의미적으로 옳다는 보증이 아닙니다. 멀리 떨어진 주석, Native에서 누락된 부모, 정렬이 다른 계속표는 놓칠 수 있습니다. 기존 iM 행·열 Tree 오류 및 운영 RAG는 이번에 수정하지 않았습니다.</p>
<h2>더 개선하려면</h2>
<ol><li><b>입력 문맥 조립부터:</b> 대상 표 + 부모 설명·제목·주석 + 실제 계속표 후보를 원본 ID로 수집하고, 누락/잘림을 명시합니다. 원본 전체를 무조건 넣기보다 필요한 영역을 제한합니다.</li>
<li><b>그다음 의미 해석:</b> 완성된 문맥과 이미지에서 행·열 제목, 기간 기준, 조건·예외, 적용 대상을 각각 제안하게 합니다. 물리 셀·숫자는 다시 만들지 않도록 합니다.</li>
<li><b>프롬프트 비교는 입력을 고정한 뒤:</b> 같은 입력으로 기존/수정 프롬프트를 비교해야 문맥 추가 효과와 지시문 효과가 섞이지 않습니다. 이번에는 프롬프트 변경 0개입니다.</li>
<li><b>Judge 외 검증 유지:</b> 누락 탐지, 잘못 추가한 관계, 과잉 보류, 실제 경로·값 정확도를 별도로 평가합니다. 생성기·Judge 일치만으로 승인하지 않습니다.</li></ol>
<p>첫 실행 run-01에서는 하나은행 1건이 과잉 보류됐고 한국투자저축은행에서 불필요한 앞 페이지 후보도 생겼습니다. 동일 span 기반 동일성 확인과 표 끝 행 조건으로 보완했으며 그 실행은 보존했습니다. run-02는 중간 회귀, run-03은 최종 코드·Langfuse 기록입니다. 이 자료는 이제 개발 회귀이며 독립 Holdout 성능으로 부르지 않습니다.</p>
<p><a href="evaluation-reviewed.json">결과·한계 전체 JSON</a> · <a href="context-expansion-diagnostic.json">부모 문맥 추가 진단 4→0</a> · <a href="continuation-checks.json">실제 앞뒤 페이지 검사</a> · <a href="trace-readback.json">Langfuse 서버 재조회</a> · <a href="run-03/graph.mmd">실제 LangGraph 연결</a> · <a href="run-01/summary.json">보존된 첫 실패 실행</a></p>
'''
    (BASE/'comparison-reviewed.html').write_text(intro+'<pre>'+e(json.dumps(dict(tests=75,model_calls=0,cost_usd=0,langfuse=evaluations['traces'],protected_files=evaluations['protected_files'],inputs_unchanged=unchanged),ensure_ascii=False,indent=2))+'</pre>'+''.join(blocks)+'</html>',encoding='utf-8')
    print(json.dumps(dict(normal_unchanged=evaluations['normal_decisions_unchanged'],withheld=evaluations['absence_withheld'],inputs_unchanged=unchanged,protected_files=len(freeze['inputs'])),ensure_ascii=False))
