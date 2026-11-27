# 실제 에이전트 팀을 연결하는 구현 계획

작성일: 2026-11-04\
상태: 단계별 구현 계획. 선택 표본의 파싱→전문 검토 연결을 구현하고 실제 호출을 시도했다. DNS 장애로 모델 응답은 받지 못했다. 아래 목표 구조 중 미구현 항목은 계속 계획이다.\
범위: 한국 금융·부동산 PDF 수집 → 파싱 → 실제 LLM 검토 → 격리된 파서 개선 → 비교·회귀 평가 → 마지막 사람 검증 → 확정·확장 실행.

## 1. 목표와 완료의 의미

사용자는 매번 문서 ID를 입력하거나 팀별 스크립트를 실행하지 않는다. 한 번 실행을 시작하면 Supervisor가 업무를 배정하고, 각 팀의 결과와 실패 원인을 기록하며, 최종 검증 자료가 준비되었을 때 사용자에게 보여준다.

사용자는 원본과 기존·후보 결과를 보고 맞음·틀림·판단 불가를 기록한다. 이 기록을 저장한 뒤 검증 조건을 만족하는 후보를 확정하고 다음 문서 묶음으로 진행한다. 틀린 사례는 다음 개선 작업으로 연결한다.

**실제 PDF와 실제 모델 응답이 같은 campaign_id 아래 연결되어야 한다.** 역할별 Python 파일, 성공한 모의 테스트, 별도 모델 호출 스크립트만으로 팀 구현 완료라고 하지 않는다. 테스트의 모의 응답은 실패·재시도 검증에 계속 사용하되, 실제 연결 검증을 대체하지 않는다.

## 2. 현재 코드에서 확인한 상태

아래 경로는 별도 표시가 없으면 `src/doc_understanding/` 기준이다.

| 영역 | 현재 코드 | 확인한 상태와 문제 |
|---|---|---|
| Supervisor | `teams/campaign/graph.py`, `policy.py` | 기본 경로는 supervisor·parse·로컬 review. 선택 pilot은 specialist 노드를 추가로 실행한다. 개선·평가·승인은 아직 미연결이다. |
| 수집 | `teams/campaign/service.py`, `collection.py`, `public_sources.py`, `public_download.py` | 로컬·외부 수집은 그래프 밖에서 실행된다. 로컬은 inbox 중심. 기존 여러 데이터셋을 통합 관리하지 않는다. |
| 외부 자료 | `public_sources.py`, `public_download.py` | 한국은행·국토연구원 후보 링크 탐색, 제한된 국토연구원 PDF 다운로드 코드가 있다. 실제 사이트 다운로드 성공까지 검증된 상태로 보지 않는다. |
| 파싱 | `services/documents.py` | 기존 worker를 재사용한다. 추출 결과와 원문 근거를 보존하는 기능은 유지한다. |
| 검토 연결 | `teams/campaign/review_requests.py`, `pilot.py` | 기존 자동 신호 경로는 local 고정. 신규 선택 pilot 경로는 현재 파서 출력에 근거를 다시 연결하고 `agents_roles`를 실제 호출한다. |
| 실제 LLM 팀 | `teams/table_review/`, `teams/evidence_review/`, `teams/axis_review/`, `agents/` | table_review를 pilot에 연결했다. 추가 전문팀과 개선·평가 연결은 미완료다. |
| 모델 실행 기반 | `runtime/review_queue.py`, `model_client.py`, `settings.py`, `budget.py` | 영속 큐·호출 기록·예산 기능을 재사용할 수 있다. 데이터 루트별 설정이 달라 캠페인과 기존 화면의 활성화 상태가 어긋날 수 있다. |
| 파서 개선 | `parser_experiments/` | 격리 실행·패치 제안·검증 코드가 있다. `automatic.py`와 `registered_plan.py`에는 조건 충족 시 작업 코드를 직접 바꾸는 경로도 있다. 마지막 사람 검증과 분리해야 한다. |
| 평가 | `teams/campaign/evaluation.py` | 호출자가 전달한 점수를 비교한다. 실제 파서 출력과 확정 정답을 불러와 계산하는 통합 평가가 아니다. `reference_status` 문자열만으로 정답 승인 여부를 신뢰한다. |
| 기존 평가기 | `eval/golden.py`, `eval/tree_accuracy.py`, `eval/ledger.py` | 물리 구조·경로 평가와 불변 이력 기능이 있다. 입력 어댑터와 캠페인 연결이 필요하다. |
| 사람 검증 | `review/error_basket.py`, `runtime/review_queue.py` | 근거 수집·참조 기록·의견·확인 기능이 있다. 캠페인 후보 승인과 같은 의미는 아니다. |
| 화면 | `web/app.py`, `web/static/campaign.js` | 시작·상태·일부 결과 표시. 프로세스 메모리의 실행 플래그와 스레드 중심이며, 전체 팀 진행·최종 확정·재개까지 완성되지 않았다. |

설계가 어긋난 지점은 **기존 개별 기능의 작성·검증을, 연결된 팀의 실행 완료로 취급한 것**이다. DNS 문제는 외부 호출의 실행 장애다. DNS를 해결해도 위 연결 누락은 별도로 구현해야 한다.

## 3. 목표 팀 구성

팀은 역할, 입력, 도구, 출력, 상태 전이가 있는 실행 단위다. 모든 단계에 LLM을 붙이지 않는다. 수치 계산과 승인 조건은 코드가 책임지고, 원문 해석·오류 가설·일반적인 수정 제안에 실제 모델을 사용한다.

| 팀 | 내부 역할 | 실행 방식 | Supervisor에게 반환할 결과 |
|---|---|---|---|
| Supervisor | 작업 관리자, 우선순위 판단자 | 상태·예산·승인 조건은 결정적 코드. 모호한 개선 우선순위는 제한된 LLM 제안 후 정책 검사 | 다음 작업, 선택 이유, 근거 ID, 잔여 예산, 중단 사유 |
| 표본 수집 | 기존 데이터 등록, 공개 자료 탐색, 다운로드·중복 검사 | 출처별 수집 도구와 정책. 임의 URL을 모델이 실행하도록 하지 않음 | 출처·이용 조건·PDF 해시·분할 역할을 갖춘 표본 목록 |
| 문서 처리 | 기준 파서 실행, 후보 파서 실행, 근거 묶음 작성 | 기존 결정적 파서 및 격리 실행기 | 파서 버전별 출력, 원본 페이지·셀 근거, 실패 페이지 |
| 구조 검토 | 역할 분류, 관계 분석, 독립 Judge | `table_review`의 실제 모델 호출을 기본 경로로 사용 | 오류 가설, 출처 근거, 불확실성, 개선 요청 후보 |
| 파서 개선 | 원인 분석·패치 제안, 격리 실행 | 실제 모델로 제한된 수정 제안. 실행기는 허용 파일·자원 제한 적용 | 패치, 후보 코드 해시, 테스트 결과, 변경 설명 |
| 비교·검증 | 물리 구조 채점, 행·열 경로 채점, 회귀 판정 | 확정 정답 기반 결정적 평가, 원문 무결성 검사 | 이전·후보 점수, 개별 실패, 평가 범위, 사람 검증 묶음 |

`evidence_review`와 `axis_review`는 초기 기본 경로와 동시에 전부 실행하지 않는다. 기본 팀으로 해결하지 못한 근거 부족·축 해석 사례에만 정책으로 선택한다. 팀별 모델·프롬프트 버전과 실제 호출 횟수는 기록한다.

### 실행 순서

1. 실행 환경·예산·사용할 데이터셋 버전을 고정한다.
2. 수집 팀이 기존 데이터와 공개 자료에서 이번 묶음을 확보한다.
3. 문서 처리 팀이 기준 파서로 처리한다.
4. 로컬 검사와 구조 검토 팀이 원문 근거를 검토한다.
5. Supervisor가 개선할 오류 묶음을 고른다. 정책상 허용되는 선택만 실행한다.
6. 개선 팀이 후보 코드를 만들고 격리 환경에서 실행한다.
7. 비교·검증 팀이 같은 입력·같은 정답·같은 기준으로 이전/후보를 평가한다.
8. 자동 검사 실패는 제한된 횟수 안에서 재작업한다. 통과 또는 자동 해결 한계 도달 시 사람 검증 묶음을 만든다.
9. 사람 검증을 기다린다. 기다리는 동안 앱을 종료해도 상태가 보존된다.
10. 검증 기록과 후보 승인 조건을 확인한 뒤 검증된 후보를 로컬 기본 버전으로 지정한다.
11. 별도로 보존한 미사용 자료로 확장 검증한다. 실패 사례를 다음 회차 개발 자료로 전환하고 변경 이력을 남긴다.

개선안을 만들지 못한 경우에도 결과를 숨기지 않는다. 기준 파서 결과·실패 근거·시도 내역을 최종 검증에 제출한다. 개선이 없는데 성공한 후보를 만들어내지 않는다.

## 4. 앞으로의 폴더와 코드 책임

기존 파서와 전문 검토 팀을 유지한다. 초기에는 파일을 대거 이동하지 않고 어댑터로 연결한다. 아래 신규 경로는 해당 단계에서 실제 책임이 필요해질 때 추가한다.

| 위치 | 기존/신규 | 책임 |
|---|---|---|
| `teams/campaign/` | 기존 확장 | 전체 실행 그래프, 정책, 상태, 지속 실행, 결과 연결 |
| `teams/campaign/contracts.py` | 신규 | Pydantic 작업·결과·결정 계약. 큰 원문은 넣지 않고 불변 artifact 참조 사용 |
| `teams/campaign/config.py` | 신규 | 수집 한도, 팀 선택, 모델 예산, 회차 제한, 평가 정책의 고정 snapshot |
| `teams/campaign/worker.py` | 신규 | 영속 작업 claim, lease·heartbeat, 재시작 복구, 취소·재개 |
| `teams/campaign/tasks.py` | 신규 | 팀 호출 어댑터. review/repair 큐 ID와 캠페인 task ID 연결 |
| `teams/campaign/decisions.py` | 신규 | 사람 검증 묶음 생성·확정·반려, 오래된 결과에 대한 승인 거절 |
| `teams/campaign/promotion.py` | 신규 | 최종 승인된 후보를 기본 파서 버전으로 선택하고 되돌리기 기록 |
| `teams/collection/service.py` | 신규 | 기존 로컬·공개 수집 함수를 하나의 팀 작업으로 실행 |
| `teams/collection/catalog.py` | 신규 | 기존 데이터셋 등록·출처 추적·중복·개발/회귀/holdout 분리 |
| `teams/table_review/` | 기존 유지·연결 | 기본 구조 검토 팀. 별도의 복제 팀을 만들지 않음 |
| `teams/evidence_review/`, `teams/axis_review/` | 기존 유지 | 정책으로 선택하는 추가 전문 검토 |
| `teams/parser_improvement/service.py` | 신규 | 캠페인 오류 묶음을 기존 파서 실험 계약에 맞춰 전달 |
| `teams/verification/service.py` | 신규 | 고정 manifest의 기준·후보 결과 수집, 평가·회귀 보고 |
| `teams/verification/adapters.py` | 신규 | 기존 출력·정답을 `golden`/`tree_accuracy` 입력으로 변환 |
| `agents/campaign_planner.py` | 신규, 후속 단계 | 후보 우선순위와 추가 근거 요청을 구조화해 제안. 승인·임의 코드 실행 권한 없음 |
| `agents/role_classifier.py`, `relation_analyst.py`, `relation_judge.py` | 기존 유지 | 이미 존재하는 실제 검토 역할 |
| `runtime/review_queue.py`, `model_client.py`, `budget.py` | 기존 확장 | 실제 호출·큐·예산의 공통 실행 기반 |
| `runtime/campaign_migrations/` | 신규 | 캠페인 SQLite 전용 Alembic 이력. 기존 업무 DB와 분리 |
| `parser_experiments/` | 기존 수정 | 모델 패치·고정 소스·격리 실행. 자동 코드 반영은 분리 |
| `eval/` | 기존 재사용·필요한 어댑터 추가 | 물리 구조, 의미 경로, 불변 평가 이력 |
| `review/error_basket.py` | 기존 재사용 | 원본 근거 및 사람이 확정한 참조 기록 |
| `web/campaign_api.py` | 신규 | 캠페인 전용 API. `app.py`는 등록과 수명주기 관리 |
| `web/static/campaign.js` | 기존 수정 | 실행·팀 상태·근거·점수·최종 검증 화면 |
| `tests/test_campaign_*.py` | 기존 확장·신규 | 전이·연결·재시작·평가·승인 테스트 |

문서 처리 팀은 `tasks.py`에서 `services/documents.py`를 호출하는 것으로 시작한다. 역할 없는 빈 `agents/` 파일이나 중복된 파싱 그래프를 만들지 않는다. `.agent/agents/`는 개발 지침이며 애플리케이션 에이전트를 생성하는 폴더가 아니다.

### 설정·자료·실행 산출물

| 제안 위치 | 보관 내용 |
|---|---|
| `configs/campaign/default.json` | 모델 이름, 팀별 한도, 반복 한도, 수집 정책. 키·토큰은 저장하지 않음 |
| `configs/campaign/sources.json` | 출처별 목록·상세·첨부 URL 규칙과 이용 조건 확인 방법 |
| `configs/campaign/acceptance-v1.json` | 지표 정의 버전, 비교 범위, 회귀 허용 정책 |
| `data/campaign-catalog/` | 기존 데이터의 등록 manifest. PDF를 임의로 이동하지 않음 |
| `work/campaigns/campaigns.sqlite3` | 캠페인·회차·작업·결정·이벤트·산출물 인덱스 |
| `work/campaigns/artifacts/<id>/` | 입력·출력·검토·평가·사람 검증 자료의 불변 파일 |
| `work/campaigns/parsers/<id>/` | 고정된 기준·후보 코드와 환경 manifest |
| 기존 `work/document-explorer/` | 기존 문서·검토 이력 보존. 캠페인에서 참조 가능하도록 어댑터 연결 |

Windows 경로 길이를 고려해 물리 경로의 ID는 짧게 둔다. 전체 SHA-256과 부모 관계는 manifest에 저장한다. 기존 산출물은 덮어쓰지 않고 새 실행 ID를 부여한다.

## 5. 우선 수정해야 할 기존 파일

| 우선순위 | 파일 | 구체적인 변경 |
|---|---|---|
| P0 | `parser_experiments/automatic.py` | 캠페인에서는 후보 생성·검사에서 종료. `apply_verified()` 직접 호출 경로와 마지막 사람 승인 경로를 분리한다. 기본 실행도 검증 전에 작업 코드를 바꾸지 않도록 계약을 정리한다. |
| P0 | `parser_experiments/registered_plan.py`, `queue.py` | `apply_to_workspace`, `apply_passing_result` 등 반영 경로 전체에서 후보·평가·최종 승인 ID를 검증. 큐 자동 reconcile이 캠페인 관리 작업을 중복 실행하지 않게 소유자를 분리한다. |
| P0 | `teams/campaign/state.py`, `store.py` | campaign만이 아니라 round/task/attempt/artifact 관계, 상태 사유, lease, idempotency key를 저장한다. 샘플 하나의 결과 컬럼만으로 여러 파서 실행을 표현하지 않는다. |
| P0 | `teams/campaign/service.py`, `graph.py`, `policy.py` | 수집도 그래프 작업으로 넣는다. `specialist_review_not_connected` 종료와 그래프 뒤 준비 작업을 없애고 실제 팀 결과를 다음 전이에 사용한다. |
| P0 | `teams/campaign/review_requests.py` | `local` 고정을 제거하고 캠페인 설정에 따른 실제 `agents_roles` 요청을 생성한다. 로컬 검사는 별도 단계로 유지. parser/result/review 버전을 포함한 중복 키 사용. |
| P0 | `runtime/review_queue.py` | 요청 생성의 멱등 키, 캠페인 소유자, task 연결을 추가한다. 요청 생성 후 mapping 저장 전 장애가 나도 같은 요청을 찾을 수 있어야 한다. |
| P0 | `runtime/settings.py`, `budget.py`, `model_client.py` | 캠페인 설정과 실제 팀 실행의 예산 기준을 일치시킨다. 전체/팀/일별 한도를 원자적으로 예약하며, 시도·응답·실패·사용량 미확정을 구분한다. |
| P1 | `teams/campaign/collection.py`, `public_sources.py`, `public_download.py` | inbox뿐 아니라 등록된 기존 데이터셋 사용. 출처 정책·문서별 이용 조건·다운로드 결과를 기록. 수집 실패가 로컬 수집 성공처럼 보이지 않게 구분한다. |
| P1 | `services/documents.py`, `parser_experiments/engine.py` | 파서 버전 선택을 실행 어댑터로 명시. 기준·후보를 같은 고정 입력에서 실행. 생성 코드는 격리 실행기를 거친다. |
| P1 | `teams/campaign/review.py` | 로컬 불확실성 신호와 실제 모델 검토 결과를 구분. 신호 없는 표도 일부 표본 검토해 자동 검사 누락을 찾는다. |
| P1 | `teams/campaign/evaluation.py` | 문자열 승인·외부 점수 입력 중심 비교에서 실제 ledger·확정 reference 조회 기반으로 변경. 모든 지표의 분모가 같아야 한다는 규칙을 제거하고 지표별 비교 계약을 적용한다. |
| P1 | `review/error_basket.py`, `runtime/review_queue.py` | 기존 참조 저장·의견·확인 기록에 campaign/round/candidate 연결. 기존 의견 확인을 후보 승인으로 재해석하지 않는다. |
| P1 | `web/app.py`, `web/static/campaign.js` | 메모리 실행 플래그를 영속 작업 상태로 대체. 최종 검증·재개·회귀 결과 화면을 추가한다. |
| P1 | `pyproject.toml` | 실제 필요한 migration/checkpoint 의존성과 패키지 파일을 검증해 추가. 기존 버전은 근거 없이 일괄 갱신하지 않는다. |
| 문서 | `docs/DESIGN.md`, `docs/AGENT_TEAMS.md`, `docs/CAMPAIGN_SUPERVISOR.md` | 구현 단계마다 현재 상태를 갱신. 계획과 실제 실행 검증을 구분한다. |

`table_understanding/physical_grid.py`, `semantic.py`, `header_evidence.py`, `table_identity.py` 등을 지금 한꺼번에 수정하지 않는다. 연결된 실험에서 원인이 확인된 파일만 후보 사본에서 수정한다. PhysicalTableIR 2.1 계약은 유지한다.

## 6. 공통 실행 계약과 복구

### 작업 입력·출력

- 입력: `campaign_id`, `round_id`, `task_id`, `team`, `dataset_manifest_id`, `parser_version_id`, `input_artifact_ids`, `config_snapshot_id`, `idempotency_key`.
- 출력: `status`, `output_artifact_ids`, `finding_ids`, `reason_code`, `usage`, `started_at`, `finished_at`.
- 모델 실행 기록: provider/model/prompt/schema 버전, request ID가 있으면 그 값, 입력·출력 해시, 호출 시도, 실제 응답 여부, 사용량 및 미확정 여부.
- Supervisor 결정: 허용된 다음 작업, 근거 artifact ID, 선택 이유, 적용 정책 버전. 모델 제안과 최종 정책 결정은 따로 기록한다.

예상 작업 상태는 `pending → running → succeeded | failed | blocked | cancelled`이다. 캠페인에는 별도로 `awaiting_human`, `paused`, `completed`를 둔다. `blocked`는 DNS·예산·격리 실행기 부재처럼 원인이 있는 대기이고, `completed`는 요청한 범위의 작업이 끝난 상태다.

### 저장과 중복 방지

1. 기존 campaigns/samples/events를 보존하고 rounds/tasks/attempts/artifacts/decisions/parser_versions 및 dispatch outbox를 추가한다.
2. 캠페인 작업 생성과 outbox 기록은 같은 SQLite transaction으로 저장한다.
3. dispatcher가 review/repair 큐에 멱등 키로 전달한다. 별도 DB 간 전송 완료 직전 중단되어도 동일 요청을 재사용한다.
4. worker는 lease·heartbeat를 가진다. 단일 worker로 먼저 구현하고, 복구 검증 후 제한된 병렬 처리를 허용한다.
5. LangGraph 상태에는 불변 참조와 다음 전이만 둔다. 업무 결과의 기준은 DB의 task/artifact 기록이다. 재시작 시 미완료 전이를 재구성하고 이미 완료한 부작용을 반복하지 않는다.
6. 외부 모델 요청 후 응답을 잃은 경우는 `outcome_unknown`으로 보존한다. 캐시·provider 조회로 확인할 수 없다면 자동 재호출하지 않는다. 실패를 성공 또는 무료 호출로 계산하지 않는다.
7. 앱 재시작·중복 클릭·오래된 worker 종료와 관계없이 새 후보·평가 파일은 원자적으로 저장한다. 임시 파일은 성공 산출물로 등록하지 않는다.

SQLite에는 전용 Alembic environment와 version table을 둔다. 기존 `migrations/env.py`는 `DATABASE_URL` 기반이므로 캠페인 DB에 그대로 실행하지 않는다. 무조건 `CREATE TABLE IF NOT EXISTS`를 추가하는 방식 대신 버전·백업·업그레이드 검증을 넣는다. 기존 review/repair DB 변경도 해당 DB의 명시적 migration으로 관리한다.

### 실행 한도

초기 기본값 제안: 회차당 최대 20문서, 외부 PDF 최대 3건, 구조 검토 최대 5표, 후보 수정 최대 2회. 모델 총호출·팀별·일별 한도와 실행 시간 제한을 별도로 둔다. 숫자는 설정 snapshot으로 고정하고 보고서에 표시한다.

부족한 예산은 사용자가 매번 재입력하는 문제로 만들지 않는다. 설정된 한도 안에서 다음 가능한 작업을 실행하고, 불가능한 작업은 정확한 사유와 함께 대기한다. 필수 LLM 검토가 실패했을 때 로컬 검사로 바꾸어 성공 처리하지 않는다.

## 7. 수집과 데이터셋 분리

### 기존 자료부터 재사용

`data/financial-property-pilot/`, `data/financial-property-fill/`, `data/quality-golden/`, `data/generalization-10-20261103/` 등은 등록 후보이다. 폴더 이름만으로 원본 PDF·정답·holdout 여부를 결정하지 않는다. manifest, 실제 파일, 기존 사용 이력을 조사하고 해시와 참조 상태를 등록한다.

원본을 복사하거나 이동하기 전에 기존 경로를 참조하는 catalog를 만든다. 같은 PDF의 다른 경로와 기관·시기·보고서 계열의 유사 문서를 구분한다. exact hash 중복 검사 외에 같은 보고서의 개정판·인접 연도 등 데이터 누출 가능성을 계열 정보로 관리한다.

### 신규 한국 자료 수집

첫 출처는 기존 코드가 있는 국토연구원과 한국은행이다. 그 뒤 한국부동산원·금융감독원 공개 자료 등은 별도 adapter와 실제 사이트 검증이 완료된 뒤 추가한다. 이 문서는 해당 출처의 현재 다운로드 방식이나 이용 허용을 확인한 문서가 아니다.

등록 정보: 기관, 문서 종류, 제목, 게시·수집 시점, 목록/상세/첨부 URL, 최종 URL, PDF 해시, 페이지 수, 이용 조건 근거와 적용 범위, 수집 성공·실패 이유.

기존 페이지 문자열 몇 개의 존재를 이용 허용의 충분조건으로 삼지 않는다. 문서별 조건과 파일 연결을 확인한다. 미확정은 링크 후보로 남기고 자동 다운로드·외부 모델 전송 가능 범위를 별도로 관리한다. 리다이렉트가 필요한 출처는 매 단계 호스트·스킴을 검증하는 adapter로 지원한다.

### 데이터 역할

| 역할 | 개선 팀 접근 | 사용 목적 |
|---|---|---|
| development | 원문·오류·정답 접근 가능 | 원인 분석·후보 수정 |
| regression | 검증기가 접근. 초기 구현은 개선 모델에 상세 정답을 주지 않음 | 기존 동작의 손상 탐지 |
| holdout | 개선 팀 접근 금지 | 고정된 후보의 미사용 자료 검증 |

여러 캠페인과 기존 parser-repair 데이터셋을 통합한 catalog에서 역할 충돌을 검사한다. 기존 campaign DB 안에서만 해시를 검사하는 것으로 끝내지 않는다.

holdout의 상세 실패를 다음 개선 입력으로 사용하면 해당 문서는 다음 버전에서 development로 전환한다. 전환 전 검증 결과는 보존하고, 후속 검증에는 새 holdout을 확보한다. 반복해서 본 회귀 자료를 범용 성능의 독립 증거로 표시하지 않는다.

## 8. 평가 기준과 회귀 기록

### 정확도를 계산할 수 있는 조건

기존에 사람이 확정한 reference가 있으면 최종 사용자 검증 전에도 그 범위의 정답 기반 점수를 계산할 수 있다. 새 문서에 reference가 없으면 자동 검사 통과율·LLM 의견·변화량만 표시한다. 이를 정확도라고 부르지 않는다.

마지막 검증에서 새 reference가 확정되면, 이미 고정해 둔 기준·후보 출력 모두를 그 동일 reference로 다시 평가한다. 후보 생성 때 사용한 자료에서 얻은 개선 수치는 개발 성능으로 표시하고 미사용 자료 성능과 분리한다.

| 지표 | 정의 | 필요한 정답과 주의점 |
|---|---|---|
| 표 탐지 precision/recall | TP/(TP+FP), TP/(TP+FN) | 해당 페이지 전체의 표 영역 주석 필요. 선택한 표 하나의 확인만으로 페이지 전체 탐지 성능을 계산하지 않음 |
| 셀 형상 | 기준 셀의 위치·행·열 일치율, 예측 누락·과잉 수 | 좌표계·허용 오차·일대일 대응 규칙 고정 |
| 병합 셀 | rowspan/colspan 정확 일치와 병합 셀 precision/recall | 없는 병합을 만든 오류와 병합 누락을 함께 기록 |
| 행 경로 | root부터 leaf까지 전체 경로의 exact match | 같은 텍스트가 반복되어도 출처·부모 관계로 구분 |
| 열 경로 | 다단 헤더의 전체 경로 exact match | 부모 헤더·기간·단위의 주석 범위 명시 |
| 값 셀 연결 | 값 셀에 연결된 행 경로와 열 경로가 함께 맞는 비율 | row/column 중 하나만 맞으면 전체 연결 성공 아님 |
| 근거 무결성 | 원문·페이지·셀·span 연결 및 원문 값 보존 | 후보가 값을 고치거나 출처를 잃으면 승인 차단 |
| 미해결/누락 | unresolved, unmatched, 파싱 실패 수와 coverage | 실패 사례를 분모에서 조용히 빼지 않음 |

기존 `golden.py`의 표 IoU 0.8, `tree_accuracy.py`의 셀 대응 규칙은 첫 기준 후보로 재사용하되 정의·한계를 읽고 metric version에 고정한다. 기준을 바꿀 때는 새 버전으로 이전 결과도 재계산해야 비교할 수 있다.

precision의 분모는 예측 개수이므로 후보 변경 시 달라질 수 있다. 따라서 `evaluation.py`의 모든 지표에 대한 동일 분모 강제는 수정해야 한다. 대신 입력 문서·주석 범위·reference 버전·평가기 버전을 동일하게 유지하고 TP/FP/FN과 누락을 비교한다. 적용 대상이 0건이면 100%가 아니라 N/A다.

### 후보 통과 조건 초안

- 원문 변조, 유실, 잘못된 출처 연결 같은 필수 무결성 위반이 없어야 한다.
- 목표 오류의 개선 근거가 있어야 한다. 정답이 없는 사례는 사람의 최종 판단을 기다린다.
- 고정 회귀 집합에서 기존에 통과한 필수 사례가 실패로 바뀌면 차단한다.
- 전체 평균이 올라도 기관·표 유형별 하락과 개별 실패를 숨기지 않는다. 초기 정책은 필수 지표 하락 허용 0으로 두되 측정 불가를 통과로 간주하지 않는다.
- 테스트·평가기·정답·승인 정책을 개선 에이전트가 수정할 수 없다.
- 최종 사람 승인 후에도 후보 코드·평가·데이터 해시가 일치해야 한다.

### 매 회차 저장할 기록

`campaign/round/task/attempt ID`, 입력 manifest·split 버전, 기준/후보 코드 해시, Python·파서 라이브러리·실행 이미지 버전, 모델·프롬프트·설정 버전, reference revision, metric version, 전체 및 기관/표 유형별 분자·분모·점수·변화량, 실패 목록, 실행 시간, 비용·사용량 미확정 상태, 사람 결정 ID를 저장한다.

정답 변경·재평가는 새 기록이다. 이전 점수를 수정하지 않는다. 회귀 이력에는 통과뿐 아니라 실패·중단·평가 불가도 남긴다.

## 9. 마지막 사람 검증과 확정

최종 화면은 한 회차의 변경 묶음을 보여준다. 문서별로 원본 PDF crop, 이전 구조, 후보 구조, 바뀐 행·열 경로, 에이전트 주장과 근거, 측정 가능한 점수, 회귀 실패를 나란히 제공한다.

사용자 입력은 다음 세 가지다.

1. **맞음**: 표시된 검증 범위를 명시하고 그 범위의 reference로 확정한다. 한 셀 확인을 표 전체 정답으로 확대하지 않는다.
2. **틀림**: 실패 사례로 저장한다. 올바른 구조가 함께 주어지지 않았다면 정답 reference로 만들지 않는다. 팀이 수정된 제안을 준비해 다음 최종 검증으로 보낸다.
3. **판단 불가**: 미확정으로 보존한다. 정답률 분모나 승인 증거로 임의 편입하지 않는다.

그다음 **검증 저장 후 확정·계속** 작업은 검증 결과 저장, 필요한 재채점, 후보 통과 조건 검사, 기본 파서 버전 선택, 다음 회차 등록을 순서대로 수행한다. 새 정보가 생겼거나 결과 해시가 바뀌면 오래된 승인을 거절하고 차이를 보여준다.

DB 결정과 기본 버전 포인터 변경은 복구 가능한 promotion 작업으로 연결한다. 중복 클릭 시 같은 결정·같은 promotion을 반환한다. 전환 전 기본 버전과 되돌릴 버전을 기록한다.

기본 방식은 검증된 후보 snapshot을 활성 버전으로 지정하는 것이다. 프로젝트 소스 덮어쓰기와 분리해 후보 검증 결과를 그대로 재현한다. 기존 작업 소스 반영 기능을 유지해야 한다면 별도 적용 영수증·기준 해시·복구 경로를 요구한다.

**검토 의견 확인, 정답 범위 확정, 파서 후보 승인, 금융 fact 발행은 서로 다른 상태다.** 이 캠페인 범위에는 금융 fact 발행·배포·원격 push가 포함되지 않는다.

## 10. 화면과 API 계획

UI는 작업 시작과 마지막 판단에 집중한다. 내부 ID와 파일 경로를 사용자가 복사해서 팀을 이어 붙이는 절차는 없앤다.

| 화면 | 표시 내용 |
|---|---|
| 실행 현황 | 목표, 현재 회차, 실제 활성 팀, 대기/실행/실패, 다음 작업과 이유 |
| 수집 현황 | 기존/신규 자료, 출처, 다운로드 완료/링크만 확보/차단 사유 |
| 검토·개선 | 실제 모델 응답 여부, 오류 근거, 후보 변경 파일, 격리 테스트 |
| 평가·회귀 | 기준/후보 버전, 지표별 점수·분모, 실패 사례, 평가 불가 범위 |
| 마지막 검증 | 원본·이전·후보 비교, 맞음/틀림/판단 불가, 확정·계속 |
| 이력 | 회차별 점수, 반려 이유, 승인 버전, 되돌리기, 재시작 기록 |

제안 API: 기존 `POST /api/campaigns/automatic`은 새 영속 시작 경로로 연결하고 호환성을 유지한다. `GET /api/campaigns/{id}`와 `/events`, `POST /pause`, `/resume`, `/cancel`, `GET /rounds/{round_id}/review-packet`, `POST /rounds/{round_id}/decision`을 캠페인 prefix 아래 추가한다. 결정 요청에는 후보·평가·packet 해시와 멱등 키를 포함한다.

초기에는 이벤트 sequence 기준 polling으로 충분하다. 실시간 전송 방식보다 상태 정확성과 종료 후 복구를 먼저 완성한다.

## 11. 구현 순서와 단계별 완료 기준

| 단계 | 구현 범위 | 완료 증거 |
|---|---|---|
| 0. 계약·안전한 반영 경계 | ADR-070 초안, 상태·artifact·최종 승인 계약, 자동 workspace 반영 경로 분리, 기존 데이터/설정/실행 환경 확인 | 사람 승인 없이 후보가 기본 코드·버전을 바꾸지 않는 테스트. 실제 호출·격리 실행 가능 여부를 원인별로 기록 |
| 1. 실제 검토까지 한 흐름 | store migration, tasks/worker, 수집→파싱→`agents_roles`→결과 수집, 기본 UI | 실제 한국 PDF 1건의 같은 campaign 아래 원문→모델 응답→Judge→결과가 연결. 모의 응답 없이 호출 증거·실패 이유 확인 |
| 2. 후보와 회귀 평가 연결 | 개선 adapter, 격리 후보 실행, reference 연결, 평가 adapter·ledger | 기준/후보 코드와 입력이 고정되고, 목표 1건 및 등록된 회귀 묶음에서 실제 결과·분자·분모·실패 기록 생성 |
| 3. 마지막 사람 검증 | review packet, reference 확정, 재채점, promotion·재개 | 맞음/틀림/판단 불가가 각각 올바른 상태로 전이. 승인 전 기본 버전 불변, 승인 후 같은 후보만 적용. 중복 클릭·재시작 검증 |
| 4. 수집·확장 루프 | 기존 catalog 통합, 실제 외부 PDF 수집, holdout 소진·역할 전환·다음 회차 | 로컬 자료와 신규 외부 PDF를 함께 사용. 승인 후 미사용 문서에서 검증하고 실패가 다음 개선으로 이어짐 |
| 5. 운영 안정화 | Supervisor의 제한된 우선순위 LLM, 조건부 전문팀, 복구·비용·병렬성 | 예산 초과·응답 유실·중간 종료·worker 충돌을 처리. 독립 문서만 제한적으로 병렬 처리 |

단계 1~3에서 작은 묶음으로 전체 연결을 완성한다. 여러 팀 폴더와 복잡한 대시보드를 먼저 만든 뒤 연결 검증을 뒤로 미루지 않는다. 단계 4의 실제 외부 수집까지 확인해야 사용자 요청 전체의 구현 완료다.

### 첫 구현 묶음의 정확한 범위

1. `docs/ADR/070-connected-campaign-runtime.md` 추가: 위 계약과 기존 경로의 호환 방식 확정.
2. `contracts.py`, `config.py`, 캠페인 migration 추가 및 `store.py` 확장.
3. `automatic.py`·`registered_plan.py`·`queue.py`의 후보 생성/적용 경계 분리.
4. `review_requests.py`의 실제 팀 연결과 큐 멱등성 구현.
5. `tasks.py`·`worker.py` 추가, `service.py`·`graph.py`·`policy.py` 연결.
6. 최소 화면 상태를 연결하고 실제 PDF 한 건으로 단계 1 종료 지점까지 검증.

환경에서 실제 호출이 실패하면 해당 결과를 실패로 남긴다. 성공한 모의 응답으로 첫 구현 묶음의 완료를 선언하지 않는다.

## 12. 검증 계획과 현재 실행 장애

### 테스트 파일 계획

| 파일 | 검증할 내용 |
|---|---|
| 기존 `tests/test_campaign_supervisor.py` | 팀 선택·필수 검토 실패·예산·승인 대기 전이 |
| 기존 `tests/test_campaign_teams_integration.py` | 하나의 campaign/task 관계로 수집·처리·검토·개선·평가 연결 |
| 기존 `tests/test_campaign_sources_evaluation.py` | 출처·중복·split 보호 및 지표별 올바른 분모 |
| 신규 `tests/test_campaign_recovery.py` | outbox 전송 중 종료, lease 만료, 중복 실행, 모델 응답 미확정 |
| 신규 `tests/test_campaign_decisions.py` | reference 범위, 오래된 승인 거절, 재채점, 중복 승인·promotion |
| 신규 `tests/test_campaign_evaluation.py` | 실제 evaluator adapter, 정답 미확정, 실패·누락·N/A, precision 분모 변화 |
| 신규 `tests/test_campaign_live.py` | 명시적으로 활성화하는 실제 PDF·실제 모델·실제 후보 실행. 보고서에 호출 및 artifact 증거 첨부 |
| 기존 검토·파서·ledger 테스트 | 기존 API·IR·확정 이력·물리/의미 파싱 회귀 |

기존 검토 큐 API 테스트 중단은 앞선 조사에서 Windows `TestClient → anyio → asyncio → socketpair/accept` 단계에서 관찰됐다. API 핸들러 오류로 확정된 것은 아니다. 최소 재현을 분리하고, 허용된 실행 환경에서 in-process ASGI 방식 또는 실제 서버 HTTP 검증으로 문제를 구분한다. 우회 후 API 계약을 검증하지 않은 채 deselect 결과를 전체 통과라고 쓰지 않는다.

앞선 실제 모델 시도는 DNS 조회 실패로 응답을 얻지 못했다. 이번 계획 작성에서는 재호출하지 않았다. 첫 실제 통합 실행 전에 모델 endpoint 연결·자격증명 존재·팀 활성화·예산을 검증한다. 키 내용은 기록하지 않는다. 외부 수집 endpoint와 모델 endpoint의 연결 상태는 따로 표시한다.

기존 개선 실행기는 offline container를 전제로 한다. container 실행 가능 여부도 별도 gate다. 불가능하면 개선 단계는 blocked로 남기며 생성 코드를 호스트에서 그대로 실행하는 fallback은 만들지 않는다.

최종 보고는 항상 다음을 분리한다: 구현된 코드, 모의 테스트 결과, 실제 실행 결과, 정답 기반 성능, 사용자 승인 여부. 이번 변경은 계획 문서 작성이며 런타임 연결·모델 호출·성능 개선을 완료했다는 의미가 아니다.

## 13. 사용자 지정 초기 표본 확정 — N03·N08

2026-11-04 사용자 지정에 따라 기존 검토 큐의 아래 13개 항목을 첫 연결 실행의 개발 표본으로 선택했다. 원본을 새로 업로드하거나 항목을 다시 입력할 필요가 없다.

선택 manifest: [n03-n08-initial-20261104.json](../data/campaign-catalog/n03-n08-initial-20261104.json). 이 파일은 선정 당시 `selected_not_executed`, `runtime_registered=false` 상태의 고정 snapshot으로 유지한다. 이후 실행 상태는 manifest를 덮어쓰지 않고 캠페인 기록에 따로 남긴다.

| 문서 | 페이지·항목 | 확인한 최신 큐 상태 |
|---|---|---|
| N03 KB퇴직연금 정기예금 | 2쪽 | `agents_roles` 실패. 오류 `DailyReviewBudgetExceeded` |
| N03 KB퇴직연금 정기예금 | 3쪽, 5쪽 | `agents_roles` 완료. 각각 모델 응답 3개 저장 |
| N03 KB퇴직연금 정기예금 | 4쪽 | 로컬 검사 완료 |
| N08 LH 광명 국민임대 | 5쪽, 12쪽 표 2개 | `agents_roles` 완료. 각각 모델 응답 3개 저장 |
| N08 LH 광명 국민임대 | 11·13·17·18·31·36쪽 | 로컬 검사 완료 |

- LH 12쪽은 `physical000`과 `physical001`로 서로 다른 표다. 페이지 번호만으로 중복 제거하지 않는다.
- 총 2문서, 12개 서로 다른 문서/페이지 조합, 13개 검토 항목이다. 셀 범위 검토와 표 범위 검토도 그대로 보존했다.
- 13개 항목 모두 원본 PDF 및 `document.json` 해시가 요청 snapshot과 일치함을 확인했다. manifest에 원본·snapshot·요청 payload·결과 해시를 기록했다.
- 선택 항목의 error-basket에는 reference 확정 이벤트가 없다. 완료 상태와 저장된 모델 응답은 정답 또는 파서 승인으로 간주하지 않는다.
- 저장된 모델 응답은 과거 검토 근거다. 이번에 새 연결 캠페인이 실제 모델을 호출했다는 증거로 재사용하지 않는다. 기존 기록 가져오기와 새 호출 결과는 따로 표시한다.

첫 실제 연결 검증은 N03 3쪽으로 시작한다. 기존 근거와 역할별 결과가 있어 팀 연결 과정에서 무엇이 바뀌었는지 비교할 수 있기 때문이다. 이후 N08 12쪽의 두 표를 개별 작업으로 연결해 같은 페이지의 서로 다른 표가 누락·합쳐지지 않는지 확인한다. N03 2쪽은 예산 상태 확인 후 제한 안에서 재시도하며, 한도를 임의로 올리지 않는다.

이 두 문서는 이미 검토·개발에 노출되었으므로 development로 고정한다. 확정된 정답이 생기면 다음 버전부터 회귀 사례로 편입할 수 있다. 범용 검증용 holdout은 별도 문서를 수집해야 한다. 이 표본 선택은 이후 한국 금융·부동산 외부 자료 수집을 대체하지 않는다.
