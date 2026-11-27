"""Report frozen outputs and independently image-read provisional sum scopes."""
import html
import json
from pathlib import Path
from doc_understanding.validation.arithmetic import compare_sum

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/cross-institution-smoke'
def read(p):
    return json.loads(p.read_text(encoding='utf-8'))
docs=[read(p) for p in sorted(OUT.glob('doc*/result.json'))]
nam=read(ROOT/'data/tree-phase3a-repair-v4/results.json')
# Annotation data authored from the rendered original p17, not resolver output.
annotation={'institution':'운주농협','page':17,'status':'provisional','authoring':'Agent visual inspection of original page under physical overlay; groups and numeric columns read independently of predicted semantic edges','human_review':False,'groups':[{'label':'비용성 소계','member_rows':[2,3,4,5,6,7,8],'total_row':9},{'label':'무비용성 소계','member_rows':[10,11,12],'total_row':13},{'label':'조달 합계','member_rows':[9,13],'total_row':14}],'columns':{'2024 금액':4,'2025 금액':6}}
unju=next(d for d in docs if d['document']['institution']=='운주농협')
physical=read(Path(unju['physical_run'])/'physical/page-017-table-000.json')
lookup={(c['row_index'],c['column_index']):c for c in physical['cells']}
checks=[]
for g in annotation['groups']:
    for period,col in annotation['columns'].items():
        members=[lookup[r,col] for r in g['member_rows']]
        total=lookup[g['total_row'],col]
        checks.append({'label':g['label'],'period':period,'members':[{'cell_id':c['cell_id'],'text':c['original_text'],'bbox':c['source_bbox']} for c in members],'total':{'cell_id':total['cell_id'],'text':total['original_text'],'bbox':total['source_bbox']},**compare_sum([c['original_text'] for c in members],total['original_text'],dash_as_zero=True,blank_as_zero=True)})
(OUT/'unju-provisional-arithmetic.json').write_text(json.dumps({'annotation':annotation,'checks':checks},ensure_ascii=False,indent=2),encoding='utf-8')
lines=['# 남원 경로 수정과 신규 기관 소규모 실행 결과','','## 남원 개발셋','','- 병합 항목명 셀의 행 경로 후보를 보완했다. 원본 StructuredTableIR 파일을 덮어쓰지 않고 새 후보 파일로 저장했다. 스키마 검증을 통과했다.','- 표 밖 부모 및 같은 열의 값 있는 동급 행 부모는 격리했다. 이는 정상 루트 확정이 아니라 기권이다. 이전 부모와 격리 사유를 trace로 보존한다.','- 동일 정답 기준 후보 화면의 셀 경로 불일치: 60 → 4, 경로 회귀 0. 후보 일치 229/233(98.3%)은 기권 후 정답 루트와 우연히 일치하는 사례도 포함하므로 확정 의미 정확도로 사용하지 않는다.','- 직접 부모 후보 일치: 48/52, 필요한 직접 부모 포함: 48/48. 행 경로 후보 일치: 69/73. 열 경로: 30/30.','- 남은 4개 셀은 p45·46의 충당금 금액 정답 영역 대응과 독립 B/A 비율 경로다. 정답 주석이나 값을 점수에 맞춰 변경하지 않았다.','- 원본 근거 일치 231/233, 기존 정답 SHA 불변. 새 API 호출 없음.','','## 신규 기관 실행 범위','','원본 5개, 259페이지의 Native Text 존재를 조사했다. 물리 표 분석은 문서당 6개 균등 페이지, 총 30페이지다. 전체 페이지 표 정확도 평가가 아니다. 최초 3개 중 스캔 2개를 그대로 보존하고 Native Text 2개를 추가했다.','기관·기간·문서유형별 고정 seed로 선정하고 원본 SHA와 코드 freeze를 저장했다. 남평·남원·강구는 제외했다. 동일 기관 기간 및 동일 SHA 중복은 차단했으나 기관 간 내용 중복의 독립 검토는 미완료다. 따라서 최종 일반화 벤치마크가 아닌 진단 실행이다.','','| 기관 / 기간 | 전체 / Native 페이지 | 탐지 후보 | 자동 기하 통과 | 부분 복원 | 실패 또는 검토 필요 |','|---|---:|---:|---:|---:|---:|']
for d in docs:
    s=d['physical_summary']
    lines.append(f"| {d['document']['institution']} / {d['document']['period']} | {d['pages']} / {d['native_text_pages']} | {s['detected_table_regions']} | {s['automatic_structure_pass']} | {s['partial_reconstruction']} | {s['failed_or_review_required']} |")
lines += ['','이 표의 자동 기하 통과는 의미 정답 인증이 아니다. 독립 Tree 정답이 없으므로 신규 문서 Parent Precision/Recall, Cell Path Accuracy, Detection Recall은 N/A다. 스캔 2개는 OCR이 없어 문자·숫자·의미 계층 복원을 주장하지 않는다.','','## 실제 관찰한 한계','','- 김천축산농협 p46: 좌우 병렬 재무상태표와 하단 페이지 번호 상자가 별도 표로 탐지됐다. 하단 번호 상자는 비재무 표 후보 오탐 사례로 수동 기록한다. 전체 오탐률은 N/A.','- 운주농협 p17: 여러 단계의 병합 항목명 열이 있다. 새 수정 모듈은 다중 항목명 열에서 기권한다. 이 유형을 지원했다고 주장하지 않는다.','- 새 실행은 기존 Rule-only Overlay와 별도 그룹/행 경로 후보를 저장했다. 후보는 검토용이고 검증된 의미 Patch로 발행하지 않았다.','- 운주 p17은 원본 이미지에서 별도로 읽은 조달 소계/합계 3개 범위 × 2개 연도 금액 열을 검산했다. 이는 provisional 주석이며 전체 문서 집계 정확도가 아니다.','','| 운주 p17 범위 | 기간 | 계산값 | 원문 | 차이 | 판정 |','|---|---|---:|---:|---:|---|']
for c in checks:
    lines.append(f"| {c['label']} | {c['period']} | {c['sum']} | {c['total']['text']} | {c['difference']} | {c['status']} |")
lines += ['','## 테스트 및 보존','','전체 pytest 219 passed, 12 subtests passed, 29.52초. Starlette/httpx deprecation 경고 1개. 샌드박스에서 로컬 HTTP 소켓 초기화가 멈췄으나 권한 승인 후 재실행하여 전체 통과했다.','원본 PDF SHA 및 동결 코드 SHA 재검증 통과. 남평 미열람, LLM/API 호출 0, OCR/Vision 미구현, GitHub Push·배포·DB 적재 없음.','원본 오버레이 일부를 시각 확인했다. 최종 HTML 브라우저 렌더링은 별도 검증하지 않았다.','','## 다음 순서','','1. 남원 p45·46 정답 영역과 독립 비율 표현을 검토하고 별도 주석 revision 여부를 결정한다.','2. 격리한 부모 관계의 정상 루트 여부를 검토한다. 기권을 정답으로 계산하지 않는 별도 승인 지표가 필요하다.','3. 신규 Native Text 3기관의 표를 독립 주석하여 Tree 정확도를 평가한다. 이번 결과로 튜닝하면 해당 기관은 개발 노출로 관리한다.','4. 스캔 자료는 OCR 도입 여부를 별도 결정한다. 현재 0 Native Text를 숫자 0으로 취급하지 않는다.']
(ROOT/'reports/NAMWON_REPAIR_AND_CROSS_INSTITUTION.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
esc=html.escape
sections=[]
for i,d in enumerate(docs):
    links=[]
    run=Path(d['physical_run'])
    for t in d['physical_summary']['tables']:
        overlay=(run/t['overlay']).relative_to(OUT).as_posix()
        links.append(f"<li>p{t['page']} 표 {t['table_index']+1} · {esc(t['state'])} · <a href='{esc(overlay)}' target='_blank'>원본 위 셀 경계 보기</a></li>")
    semantic=(Path(d['semantic_run'])/'index.html').relative_to(OUT).as_posix()
    sections.append(f"<section><h2>{esc(d['document']['institution'])} · {d['document']['period']}</h2><p>전체 {d['pages']}페이지 / Native Text {d['native_text_pages']}페이지 / 평가 페이지 {d['evaluated_pages']}</p><p><a href='{semantic}'>기존 Rule 결과</a> · <a href='doc{i}/candidate-repairs.json'>수정 후보 JSON</a> · <a href='doc{i}/result.json'>실행 결과 JSON</a></p><ul>{''.join(links)}</ul></section>")
page="<!doctype html><html lang='ko'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>남원 수정과 5기관 실행</title><style>body{font:17px/1.7 system-ui;background:#f3f6f4;color:#18372e;max-width:1100px;margin:auto;padding:24px}section{padding:24px;background:white;border:1px solid #cbd8d0;margin:20px 0}a{color:#176650}h1{font-size:28px}h2{font-size:22px}</style><h1>남원 수정과 신규 5기관 실행</h1><p>남원 후보 경로 불일치 60 → 4. 일부는 관계 기권에 따른 변화이므로 확정 정확도 향상으로 해석하지 않습니다.</p><p>신규 문서는 문서당 6개 페이지의 소규모 실행입니다. 독립 정답 미구축으로 Tree 정확도는 N/A입니다. 남평은 열지 않았습니다.</p><p><a href='../../reports/NAMWON_REPAIR_AND_CROSS_INSTITUTION.md'>상세 보고서</a> · <a href='../tree-phase3a-repair-v4/results.json'>남원 전후 결과와 남은 오류</a> · <a href='unju-provisional-arithmetic.json'>운주 원본 검산 6건</a></p>"+''.join(sections)
(OUT/'index.html').write_text(page,encoding='utf-8')
print(json.dumps({'documents':len(docs),'unju_arithmetic':[(c['label'],c['period'],c['status'],c['difference']) for c in checks]},ensure_ascii=False))
