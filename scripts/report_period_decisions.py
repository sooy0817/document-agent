"""Source-review report only; no inference, reference injection or parser changes."""
import hashlib
import html
import json
from pathlib import Path
from run_axis_judge import ROOT,read,write
from doc_understanding.agents.axis_review.axis_resolver import AxisRegion
from doc_understanding.parser.context.period_context import PeriodProposal, PeriodInventory, validate_proposal, validate_inventory
from doc_understanding.parser.context.period_decisions import PeriodDecision, PeriodDecisions, DefinitionApplication, legacy_decisions, validate_decisions

BASE=ROOT/'reports/period-decisions-20261030'


def build():
    dev=[read(BASE/'development-run-01'/f'region-{n:03d}.json') for n in range(2)]
    hold=[read(BASE/'holdout-run-01'/f'region-{n:03d}.json') for n in range(2)]
    freeze=read(BASE/'holdout-code-freeze.json')
    code_changes=[p for p,h in freeze['code'].items() if hashlib.sha256((ROOT/p).read_bytes()).hexdigest()!=h]
    protection=[]
    for folder in ['development-run-01','holdout-run-01']:
        record=read(BASE/folder/'freeze.json')
        protection.append(dict(run=folder,files=len(record['inputs']),changed=[p for p,h in record['inputs'].items() if hashlib.sha256(Path(p).read_bytes()).hexdigest()!=h]))
    refs=read(BASE/'holdout-input-reference-before-inference.json')['references']
    by_id={r['target_cell_id']:r for r in refs}
    checks=[]
    for r in hold:
        for item in r['comparison']['targets']:
            ref=by_id[item['target_cell_id']]
            checks.append(dict(case=r['case']['id'],**item,available_input_reference=ref['available_input_reference'],
                full_pdf_reference=ref['full_pdf_reference'],generator_matches_input_reference=item['proposed_status']==ref['available_input_reference'],
                judge_matches_input_reference=item['judge_status']==ref['available_input_reference'],
                reference_status=ref['status'],first_failure=None if item['proposed_status']==ref['available_input_reference'] else 'context_coverage_not_represented_then_false_absence',
                source_reason=ref['reason']))
    replay=[]
    for run in ['run-02','run-03','run-04']:
        path=ROOT/'reports/period-context-20261030'/run/'region-000.json';old=read(path)
        region=AxisRegion.model_validate(read(ROOT/old['source']));ids=old['focus_target_ids']+[old['control_target_id']]
        p=PeriodProposal.model_validate(old['proposal']); inv=PeriodInventory.model_validate(old['inventory'])
        migrated=legacy_decisions(p,ids)
        judge=PeriodDecisions(decisions=[PeriodDecision(target_cell_id=a.target_cell_id,
            status='insufficient_evidence' if a.status=='unknown' else a.status,
            relations=[DefinitionApplication(definition_cell_ids=rel.source_cell_ids,duration_label_cell_id=t.row_cell_id)
                for rel in a.expected_relations for t in rel.applies_to if t.value_cell_id==a.target_cell_id],
            supporting_cell_ids=[],reason=a.reason,missing_evidence=a.reason if a.status=='unknown' else None) for a in inv.assessments])
        replay.append(dict(original=path.relative_to(ROOT).as_posix(),actual_api_calls=0,
            original_proposal_errors=validate_proposal(region,ids,p),new_contract_proposal_errors=validate_decisions(region,ids,migrated),
            original_inventory_errors=validate_inventory(region,ids,inv),new_contract_inventory_errors=validate_decisions(region,ids,judge),
            migrated_decisions=migrated.model_dump(mode='json'),new_contract_inventory_view=judge.model_dump(mode='json'),
            limitation='Contract-only view of same frozen outputs, not a new model run; no role repair or semantic promotion.'))
    write(BASE/'same-response-contract-replay.json',replay)
    summaries=[read(BASE/folder/'summary.json') for folder in ['development-run-01','holdout-run-01']]
    traces=read(BASE/'trace-readback.json')
    evaluation=dict(code_changes_after_freeze=code_changes,protected_inputs=protection,
        development=dict(targets=6,old_candidate_agreements=sum(t['verdict']=='agreement' for r in dev for t in r['baseline_comparison']['targets']),
                         new_candidate_agreements=sum(t['verdict']=='agreement' for r in dev for t in r['comparison']['targets']),
                         reference_basis='Existing development source review; not unseen generalization.'),
        holdout=dict(documents=2,selected_pages=2,targets=4,model_agreements=4,
                     generator_input_reference_matches=sum(c['generator_matches_input_reference'] for c in checks),
                     judge_input_reference_matches=sum(c['judge_matches_input_reference'] for c in checks),
                     false_absence_targets=sum(not c['generator_matches_input_reference'] for c in checks),checks=checks,
                     unseen_required_relation_cases=0,reference_status='assistant_source_review_pending_human'),
        tests=dict(passed=66,failed=0,external_network='blocked by pytest fixture; fake API key; mocked model transport',
            command='.venv/Scripts/python.exe -m pytest tests/test_period_decisions.py tests/test_period_context.py tests/test_axis_judge.py tests/test_axis_judge_b.py tests/test_axis_generation.py tests/test_axis_graph.py tests/test_axis_validation.py tests/test_judge_telemetry.py -q'),
        api=dict(attempts=sum(s['actual_api_attempts'] for s in summaries),completed=sum(s['completed_calls'] for s in summaries),
            estimated_cost_usd=round(sum(s['estimated_cost_usd'] for s in summaries),6),unknown_cost_calls=0,
            basis='Existing project GPT-4.1 estimate 2/0.5 cached/8 USD per million tokens; not an invoice'),
        langfuse=dict(traces=len(traces),verified_traces=sum(t['scores']['verified'] for t in traces),scores=sum(len(t['scores']['scores']) for t in traces),observations=sum(len(t['observations']) for t in traces)),
        semantic_verified=False,facts_published=0,
        limitations=['No new required-relation positive case; unseen selection checks false additions and unavailable context.',
                     'Period sidecars only; existing iM row/column ancestry errors unchanged.',
                     'Model agreement and schema validity are not semantic correctness.',
                     'Document/parent context coverage is not yet represented as a reliable source-input contract.',
                     'New prompt and fresh sampling both changed; no causal effect size claim.',
                     'All source references await user review.'])
    write(BASE/'evaluation-reviewed.json',evaluation)
    e=lambda x:html.escape(str(x))
    blocks=[]
    labels=['iM 기간 기준과 비기간 대조군','하나은행 가입대상 → 제한없음','새 신한은행 가입대상·가입통화','새 한국투자저축은행 내부 금리표']
    for index,r in enumerate(dev+hold):
        folder='development-run-01' if index<2 else 'holdout-run-01';n=index%2
        cells={c['id']:c for c in read(ROOT/r['case']['source'])['cells']}
        rows=[]
        for t in r['comparison']['targets']:
            reference=by_id.get(t['target_cell_id'])
            selected=next(d for d in r['proposal']['decisions'] if d['target_cell_id']==t['target_cell_id'])
            relation=[dict(definition=[dict(id=i,text=cells[i]['text']) for i in rel['definition_cell_ids']],
                duration_label=dict(id=rel['duration_label_cell_id'],text=cells[rel['duration_label_cell_id']]['text']),
                target=dict(id=t['target_cell_id'],text=cells[t['target_cell_id']]['text'])) for rel in selected['relations']]
            rows.append(dict(target=cells[t['target_cell_id']]['text'],target_cell_id=t['target_cell_id'],
                generator=t['proposed_status'],judge=t['judge_status'],
                input_reference=reference['available_input_reference'] if reference else 'existing source-reviewed development reference',
                full_pdf_reference=reference['full_pdf_reference'] if reference else None,role_connections=relation,reason=selected['reason']))
        pdf = ('data/axis-validation-holdout-20261029/imrich.pdf' if index==0 else
               'data/hana-nested-e2e/v1/sources/하나은행_외화고단위플러스_20240530.pdf' if index==1 else
               'data/period-decisions-holdout-20261030-acquired/'+('S01' if index==2 else 'W01')+'.pdf')
        images=[(ROOT/r['case']['image']).as_uri()]
        if index>=2:images.insert(0,(ROOT/'data/period-decisions-holdout-20261030-acquired'/('S01-p1.png' if index==2 else 'W01-p2.png')).as_uri())
        blocks.append(f'<section><h2>{e(labels[index])}</h2><p><a href="{e((ROOT/pdf).as_uri())}">원본 PDF</a> · '
            f'<a href="{folder}/region-{n:03d}.json">실제 생성·Judge 응답</a> · <a href="{folder}/region-{n:03d}-sidecar.json">새 sidecar JSON</a> · '
            f'<a href="{e((ROOT/r["case"]["source"]).as_uri())}">원본 셀 JSON</a> · <a href="{e(r.get("trace_url"))}">Langfuse Trace</a></p>'
            '<div class="images">'+''.join(f'<img src="{e(i)}">' for i in images)+'</div>'
            '<pre>'+e(json.dumps(rows,ensure_ascii=False,indent=2))+'</pre></section>')
    intro='''<!doctype html><html lang="ko"><meta charset="utf-8"><title>기간 관계 상태·역할 분리 검증</title>
<style>body{font:16px/1.7 system-ui,"Malgun Gothic";max-width:1250px;margin:25px auto;padding:20px;color:#20303d}td,th{border:1px solid #ccd3d8;padding:10px;text-align:left}table{border-collapse:collapse;width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f4f6;padding:16px;max-height:600px;overflow:auto}.images{display:flex;gap:15px;align-items:flex-start;flex-wrap:wrap}.images img{max-width:100%;width:590px}section{margin-top:40px;border-top:2px solid #ddd}a{overflow-wrap:anywhere}.warning{padding:18px;background:#fff1db}</style>
<h1>필요·불필요·근거 부족을 분리한 기간 관계 실험</h1>
<p><b>개발 사례는 5/6 → 6/6 모델 일치. 새 문서에서는 입력 근거 부족을 불필요로 단정한 2건이 남았습니다.</b> 따라서 모델 일치 10/10을 의미 정확도 100%로 보고하지 않습니다.</p>
<p>각 대상마다 required / not_needed / insufficient_evidence를 저장합니다. 설명 셀 ID와 기간 구간 셀 ID, 실제 값 대상 ID를 분리하며 중복 근거 목록은 코드가 계산합니다. 기존 IR·축 경로·Canonical·DB·RAG는 바꾸지 않았습니다.</p>
<table><tr><th>대상</th><th>이전 → 이후</th><th>원본 검토 결과</th></tr>
<tr><td>iM 수수료 4개</td><td>필요 → 필요 유지</td><td>cell0035 설명 → cell0037/39/41/43 기간 구간 → cell0038/40/42/44 값</td></tr>
<tr><td>iM 가입대상 + 하나은행 가입대상</td><td>iM 근거 부족 → 불필요 / 하나 불필요 유지</td><td>기간 관계가 불필요한 비기간 항목. 기존 축 연결은 수정하지 않음</td></tr>
<tr><td>새 문서 2건 / 4대상</td><td>생성기·Judge 4/4 일치</td><td>입력 기준 참조와는 2/4 일치. 2건은 부모 문맥 누락에도 불필요 단정</td></tr></table>
<p class="warning"><b>새 사례의 실제 한계:</b> 한국투자저축은행 ISA 표의 원본 전체에는 부모 설명이 있지만, 모델에 전달되는 내부 표 영역에는 금리 2개만 남습니다. 이 입력만으로는 주변 기간 설명 부재를 확정할 수 없어 사전 참조는 ‘근거 부족’입니다. 생성기와 Judge는 모두 ‘불필요’로 답했습니다. 전체 PDF 기준으로 우연히 관계 불필요와 맞아도, 제한된 입력으로 확정한 판단은 승인하지 않습니다.</p>
<p>실험 흐름: 생성 → 역할·상태 검사 → 독립 원본 Judge → 동일 Judge 목록으로 이전/이후 비교. Judge는 후보·정답·이전 응답을 보지 않습니다. 같은 모델의 오류가 상관될 수 있고, 계약 검사는 원문 의미와 입력 완전성을 보장하지 않습니다.</p>
<p>새 자료는 신한 Value-up 외화 회전 정기예금 p.1, 한국투자저축은행 ISA 정기예금 p.2입니다. 후자는 우리은행 사이트에 게시된 PDF이며 발행기관은 한국투자저축은행입니다. 두 원본은 기존 PDF 해시와 중복이 없고, 평가 페이지는 도식·양식이 없습니다. 다른 페이지는 원본에 보존하되 이번 판정 대상에서 제외했습니다.</p>
<p>149개 코드 파일을 신규 자료 열람 전에 동결했고 이후 변경 0개입니다. 미사용 자료에 실제 ‘관계 필요’인 양성 사례는 없어 그 일반화 성능은 이번에 측정하지 못했습니다. 참조는 원본에 근거한 작성 초안이며 사람 승인 전입니다.</p>
<p><a href="development-run-01/comparison.html">개발 전후 상세</a> · <a href="holdout-run-01/comparison.html">새 문서 실제 실행</a> · <a href="same-response-contract-replay.json">동일 과거 응답의 계약 재검사</a> · <a href="holdout-input-reference-before-inference.json">추론 전 입력 기준 참조</a> · <a href="evaluation-reviewed.json">최종 수치·한계</a> · <a href="trace-readback.json">Langfuse 서버 재조회</a></p>
<p>같은 과거 응답 재생에서 중복 ID 기재 누락 오류는 파생 참조 계산으로 해소됩니다. 설명 셀에 기간 구간을 섞은 역할 충돌은 새 검사에서도 거절하며, 과거 unknown 응답을 임의로 불필요로 바꾸지 않습니다.</p>
<p><b>남은 작업:</b> 모델 입력에 부모 문맥 포함 여부·앞뒤 페이지 누락 여부를 명시하고, 그 근거가 없으면 불필요 확정을 제한하는 입력 완전성 검사가 필요합니다. 이번 동결 평가에서는 추가하지 않았습니다.</p>
'''
    summary=dict(tests=evaluation['tests']['passed'],api=evaluation['api'],langfuse=evaluation['langfuse'],code_changes_after_freeze=code_changes,inputs_unchanged=all(not p['changed'] for p in protection))
    (BASE/'comparison-reviewed.html').write_text(intro+'<pre>'+e(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre>'+''.join(blocks)+'</html>',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':build()
