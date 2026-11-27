# Phase 2-A — Physical Grid Reconstruction 구현 및 평가

> 후속 품질 개선: [골든·변경 전후·실패 사례](../reports/PHASE2A_QUALITY_IMPROVEMENT.md).
> 현재 PhysicalTableIR 2.1, 전체 후보 30개, 자동 통과 1개, partial 29개. 전체 pytest 61개 통과.

> 최신 실제 문서 평가: [강구농협 전체 PDF 평가](../reports/GANGGU_REAL_EVALUATION.md).
> 로컬 원본으로 38페이지 처리 완료: 후보 33, 자동 검증 통과 1(정확도 미입증), 부분 32.
> 아래 다운로드 차단 내용은 이전 실행 기록이다. 현재 회귀 51 + API 2개 통과, subtests 12개 통과.

작성: 2026-10-16. 코드 버전 0.2.0. 원본 설계와 .agent 지침은 보존했다.

## 결과 범위

7개 물리 복원 컴포넌트와 문서 전체 평가 CLI를 구현했다. 합성 PDF의 실제 추출·복원·JSON·PNG
생성을 수행했다. 의미 Row/Column Tree, Template Matcher, LLM Router, 금융 정규화/Fact 적재는
구현하지 않았다. 기존 Phase 1의 PyMuPDF 추출에는 drawing_stroked 메타데이터만 추가했다.
채워진 사각형의 외곽을 실제 선분으로 오인하지 않기 위한 변경이다.

실제 첨부 강구 공시는 Library에서 38페이지/616,698 bytes로 확인했지만, 다운로드 서버 DNS
접근이 차단되어 원본 바이트를 로컬 도구에 전달하지 못했다. **실문서 전체 평가·오버레이는 미실행**이다.
텍스트를 읽을 수 있다는 이유로 벡터 선분 또는 픽셀 분석을 했다고 주장하지 않는다.

## 구현 모듈

모듈 경로 접두어: src/doc_understanding/table_understanding/

| 파일 | 책임 |
|---|---|
| physical_models.py | physical-table-ir/2.0, 정책, 선분·교점·경계·행열 interval·복원 셀·원본 링크 모델 |
| region_detector.py | 교차/접촉한 선분의 연결 성분으로 독립 표 영역 탐지; 별도 region hint 지원 |
| line_detector.py | vector l/re/qu 명령과 grayscale 연속 run에서 가로/세로 선분 탐지 |
| intersection_analyzer.py | 실제 교점 및 elementary boundary의 선분 coverage/present·absent·uncertain 판정 |
| boundary_reconstructor.py | absent 내부 경계를 통과하는 slot 연결; 닫힌 직사각형 여부 확인 |
| span_resolver.py | 확정된 영역의 실제 점유 interval로 rowspan/colspan 계산; 불명확하면 null |
| row_alignment.py | bbox와 행·열 interval의 교차로 점유 범위 계산; 의미적 부모 관계 생성 없음 |
| physical_validator.py | slot 중복/누락, span 충돌, 외곽·내부 경계, 원문 변경, 원본 참조 검증 |
| physical_grid.py | 위 단계를 실행하고 물리 셀과 source cell/span 연결; 페이지별 결과·진단 생성 |
| contracts.py | PhysicalTableIR 구현 계약 재노출; 의미 구조 계약은 계속 초안 |

추가 파일: extraction/physical_evidence_adapter.py(픽셀 SDK 어댑터), eval/physical.py(전체 페이지
평가 및 원본 위 오버레이), tests/physical_fixtures.py, tests/test_physical_grid.py,
scripts/evaluate_physical_fixtures.py, scripts/evaluate_ganggu.ps1.

원본 CellIR은 변경하지 않는다. PhysicalTableIR에 source_cells와 exact/overlap/unresolved
source_links를 함께 둔다. 표 영역 밖에 남는 원본 셀은 PhysicalPageResult.unassigned_source_cells로
보존한다. 모든 슬롯은 resolved cell 또는 unresolved 영역 중 하나에 속해야 한다.

## 병합 판정

다른 열에서 관찰되는 가로선이 행 경계를 제공하고, 다른 행에서 관찰되는 세로선이 열 경계를
제공한다. 전체가 없는 내부 경계를 통과해 연결되는 slot이 직사각형이며 외곽이 실제 선으로
닫혀 있을 때만 span을 확정한다. 일부만 존재하는 경계, L자 연결 영역, 외곽이 열린 영역은
확정하지 않는다. 기본 tolerance=0.75 PDF point는 픽셀화·수치 오차용이며 긴 선분 누락을
메우는 규칙이 아니다. 항목명/페이지 번호/재무 의미를 판정에 사용하지 않는다.

완전히 사라진 선과 의도된 병합은 도형만으로 구분 불가능할 수 있다. 자동 검증은 관찰된
격자와의 일관성을 확인하며 원래 편집 프로그램의 병합 속성이나 회계 의미를 증명하지 않는다.

## 테스트 결과와 출처

| 결과 | 출처 |
|---|---|
| Phase 1: 36 passed, 12 subtests, 1 warning, PDF/API 11개 통과 | 사용자가 일반 PowerShell에서 실행했다고 제공한 결과 |
| PostgreSQL 16 healthy, pg_isready 성공 | 사용자 제공 결과; Codex SQL 질의 검증 아님 |
| 기존 PDF 통합 9 passed, 2 subtests | Codex .venv 재실행. reports/phase1-pdf-tests.xml |
| 현재 API 2개 | Codex에서는 TestClient 첫 테스트에서 진행이 멈춰 중단. 통과/실패 assertion 결과 없음 |
| API 제외 현재 전체: 50 passed, 12 subtests | Codex 직접 실행. reports/phase2a-tests.xml |
| Ruff | src/tests/scripts 검사 통과 |
| 문법·JSON Schema | compileall 및 schema exporter 실행 성공 |

현재 전체 pytest는 API 실행 정지 때문에 Codex 환경에서 완료하지 못했다. 실행한 50개에서
실패는 0개다. API 2개를 합산하여 52 passed라고 보고하지 않는다. 이전 사용자 로그는 Phase 1
기준선의 근거로 보존한다. API 동작 로직은 이번에 변경하지 않았다.

첫 synthetic 실행에서는 raster T자 교점의 끝부분 픽셀(0.3pt 정도)을 내부 경계 조각으로
오인하여 merge가 partial로 나왔다. 설정 tolerance 이하의 endpoint 겹침을 제외하도록 수정하고
vector/raster 두 경로의 회귀를 통과했다. 그 외 첫 lint 오류도 수정 후 재검증했다.

## 합성 PDF 평가

실제 생성물은 reports/physical-fixtures/에 있다. evaluation.json은 각 실행 위치와 표별
summary를 포함하며 각 실행 디렉터리에 source.pdf, document-ir.json, 페이지 PNG, 표 JSON,
선분 JSON, 오버레이 PNG가 있다. 골든 partition은 fixture 생성기/정답 JSON에만 있으며
프로덕션 코드가 이를 import하지 않는다.

| Fixture | 탐지 영역 | 자동 구조 검증 통과 | 부분 복원 | 설명 |
|---|---:|---:|---:|---|
| regular | 1 | 1 | 0 | 4×4, 빈 셀 보존 |
| horizontal | 1 | 1 | 0 | 3열 가로 병합 |
| vertical | 1 | 1 | 0 | 3행 세로 병합 |
| partial_horizontal | 1 | 1 | 0 | 일부 열에만 가로 구분선이 있는 정상 병합 |
| nested | 1 | 1 | 0 | 서로 다른 범위의 중첩 행 그룹 |
| parallel | 2 | 2 | 0 | 서로 떨어진 좌우 영역 분리 |
| incomplete | 1 | 0 | 1 | 끊긴 경계, span=null |
| borderless | 0 | 0 | 0 | 선 없는 표는 자동 격자 생성 안 함. 탐지 실패/지원 한계로 별도 기록 |
| vertical-scan | 1 | 1 | 0 | 벡터가 없는 합성 스캔의 픽셀 선분 |
| nested-scan | 1 | 1 | 0 | 픽셀 기반 점유 span이 vector 결과와 일치 |
| 합계 | 10 | 9 | 1 | 복원 실패 0; 별도로 선 없는 표 1개 미탐지 |

borderless의 0개 탐지를 성공으로 세지 않는다. region hint가 있을 때는 review_required로
원본을 유지하는 경로를 테스트했다. 별도의 nonrectangular fixture는 L자 slot 연결을 재현해
unresolved/null span을 확인했다. 이 사례는 위 10종 출력 묶음 외의 단위 회귀다.

### 요청한 포함/제외 회귀

아래는 **합성 도형의 골든 테스트 결과**이며 실제 강구 PDF를 대조한 결과가 아니다.
row_index는 0부터 시작하고 end row는 포함하지 않는다.

| 그룹 셀 | 점유 행 [start,end) | 대상 셀 행 | 기대/실제 |
|---|---|---:|---|
| 차입금 | [2,4) | 일반차입금 3 | 포함 / 통과 |
| 대출금 | [0,2) | 외상매출금 1 | 포함 / 통과 |
| 비유동 | [0,4) | 무수익성 기타 4 | 제외 / 통과 |

같은 도형에서 label을 전부 X0/X1/...로 바꿔도 bbox·span·slot 결과가 같음을 테스트했다.
이 비교는 물리적 행 범위 포함 여부이며 의미적인 Row Tree를 생성하지 않는다.

## 실제 PDF 전체 평가 방법

현재 source 접근 상태와 전체 문서 집계는 reports/ganggu-evaluation-status.json에 별도로
기록했다. 미실행이므로 탐지 수·성공 수·부분 수·실패 수는 모두 null이다. 0으로 쓰지 않았다.

프로젝트의 tests/fixtures/private/ganggu_2025_regular.pdf에 원본을 두거나 명령에 경로를 전달한다.

```powershell
.\scripts\evaluate_ganggu.ps1 -PdfPath 'C:\path\regular-disclosure.pdf'
```

내부 명령은 다음과 같다. focus-pages는 평가 순서만 바꾸며 이후 모든 페이지를 처리한다.

```powershell
.\.venv\Scripts\python -m doc_understanding physical-grid --pdf 'C:\path\regular-disclosure.pdf' --output-dir data/real-evaluation --focus-pages 8,14,15,18
```

결과: data/real-evaluation/<run_uuid>/physical/summary.json 및 page-XXX.json,
page-XXX-table-YYY.json, page-XXX-lines.json, page-XXX-overlay.png.

이미지 기반 별도 평가에는 `--mode raster`를 사용한다. 기본 auto는 vector가 없으면 픽셀 경로를
선택한다. original_text는 OCR을 수행하지 않는 스캔에서 null로 보존한다.

summary는 detected_table_regions, automatic_structure_pass, partial_reconstruction,
failed_or_review_required를 집계한다. accuracy_proven=false와 수동 평가 여부를 항상 함께
표시한다. 미탐지 페이지 목록은 ‘표가 없다’는 판정이 아니며 탐지 recall은 수동 정답 없이는 unknown이다.

## 남은 작업 / Phase 2-B gate

1. 실제 원본 파일을 전달해 8·14·15·18페이지를 먼저 실행하고 전체 38페이지를 처리.
2. 실제 표 영역·병합 bbox의 수동 골든을 만들고 오버레이를 대조. 자동 검증 통과 수와 정답률 분리.
3. 큰 페이지의 선분 budget, 색 선/점선/기울어진 스캔, 열린 외곽·선 없는 표를 보강.
4. 실제 표의 vector/raster 불일치가 있으면 강제로 합치지 않고 근거와 후보를 남기는 정책 확장.
5. 일반 PowerShell에서 현재 전체 pytest 실행 완료 여부 확인(API 환경 제한 구분).
6. 그 후 검증된 PhysicalTableIR을 입력으로 다중 구조 특성, 의미적 Row/Column Tree,
   cell mapping과 주석 scope를 Phase 2-B에서 구현. 물리 상태 partial이면 의미도 확정하지 않는다.
