"""Post-run source review, separate from inference inputs and frozen results."""
import hashlib
import html
import json
from pathlib import Path
from run_period_expansion import ROOT,read,write
from doc_understanding.parser.context.period_decisions import PeriodDecision, edges

if __name__=='__main__':
    base=ROOT/'reports/period-expansion-20261103';live=base/'live-01';rows=[]
    for path in sorted(live.glob('case-???.json')):
        r=read(path);old=read(ROOT/r['case']['saved']);prior={d['target_cell_id']:d for d in old['proposal']['decisions']}
        judge={d['target_cell_id']:d for d in (r['final']['guarded_inventory'] or {}).get('decisions',[])}
        for d in (r['final']['guarded_proposal'] or {}).get('decisions',[]):
            reference=prior[d['target_cell_id']]
            unchanged=(d['status']==reference['status'] and edges(PeriodDecision.model_validate(d))==edges(PeriodDecision.model_validate(reference)))
            reason=('금리 선택 기준일·산식은 기간 산정의 시작/종료 정의와 다름. 부모 셀에 이율 적용 설명은 있으나 별도 기간 정의로 연결할 근거는 없음.'
                if r['case']['id']=='W01' else '기존 원본 검토 대상의 상태와 관계 셀 ID를 유지함. 전체 축/문서 의미 승인은 아님.')
            rows.append(dict(case=r['case']['id'],target_cell_id=d['target_cell_id'],
                reference_status=reference['status'],predicted_status=d['status'],judge_status=judge.get(d['target_cell_id'],{}).get('status'),
                source_review_match=unchanged,reason=reason,reference_type='assistant_source_review_draft_pending_human'))
    summary=dict(targets=len(rows),source_review_matches=sum(r['source_review_match'] for r in rows),
        model_state_agreement=sum(r['predicted_status']==r['judge_status'] for r in rows),
        wrong_period_relations=[r for r in rows if not r['source_review_match']],
        independent_gold=False,holdout=False,full_row_column_accuracy_measured=False,
        reference_source='reports/period-decisions-20261030/holdout-sources/selection-before-extraction.json',
        raw_results_preserved=True,automatic_publication=False)
    write(base/'evaluation-reviewed.json',dict(summary=summary,targets=rows))
    esc=lambda x:html.escape(str(x))
    banner='''<h2>原文 검토 결론</h2><p><strong>문맥 수집 성공과 의미 판단 성공은 다릅니다.</strong>
한국투자저축은행 두 값에서 빠진 부모 셀의 4개 span은 모두 수집됐습니다.
하지만 생성기와 Judge가 금리 선택 기준·산식을 기간 산정 정의로 오해했습니다.
모델 상태 일치는 10/10이지만 기존 원본 검토 초안과의 일치는 8/10입니다.
독립 Gold나 미사용 Holdout 결과가 아니며, 전체 row/col 경로 정확도도 아닙니다.</p>
<p>기존 입력의 새 재실행에서도 W01 생성기가 단순 ‘보유기간’ 헤더를 기간 정의로 잘못 선택했습니다.
동일 프롬프트에서도 이전 응답과 달라졌으므로 변화 전체를 문맥 확장의 인과 효과로 해석할 수 없습니다.</p>
<p>계속표 원문 수집 점검: 37→14개, 31→2개 누락 span. 남은 근거가 있으므로 보류 유지.
해당 인접 페이지에는 도식이 있어 문맥 전달 진단만 했고 의미/LLM 평가에서 제외했습니다.</p>
<p>다음 개선 후보: 관계를 기간 산정 정의·금리 선택 시점·계산식으로 구분하고
정확한 원문 span/인용을 요구한 뒤 동일 후보로 검증. 이번 동결 실험에서는 프롬프트를 수정하지 않았습니다.</p>'''.replace('原文','원본')
    banner+='<p><a href="../evaluation-reviewed.json">원본 검토 결과 JSON</a> · <a href="../continuation-01/summary.json">계속표 수집 결과</a></p>'
    content=(live/'comparison.html').read_text(encoding='utf-8')
    content=content.replace('<h1>부모·계속표 문맥 수집 → 최대 1회 재판단</h1>',
        '<h1>부모·계속표 문맥 수집 → 최대 1회 재판단</h1>'+banner)
    # Keep links relative to the immutable live run and retain the unreviewed report.
    (live/'comparison-reviewed.html').write_text(content,encoding='utf-8')
    frozen=read(base/'prepared-03/freeze.json')
    write(base/'verification.json',dict(tests=81,
        test_command='.venv/Scripts/python.exe -m pytest tests/test_period_expansion.py tests/test_period_coverage.py tests/test_period_decisions.py tests/test_period_context.py tests/test_axis_judge.py tests/test_axis_judge_b.py tests/test_axis_generation.py tests/test_axis_graph.py tests/test_axis_validation.py tests/test_judge_telemetry.py -q',
        protected_input_count=len(frozen['inputs']),inputs_unchanged=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in frozen['inputs'].items()),
        code_frozen=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in frozen['code'].items()),
        expanded_W01_image_visually_reviewed=True,browser_html_visual_review=False,
        preparation_failures_preserved=['prepared-01: unavailable Pillow; reused installed PyMuPDF renderer',
            'prepared-02: original empty Physical table; retained in inventory diagnostics, unavailable for context collection']))
    print(json.dumps(summary,ensure_ascii=False))
