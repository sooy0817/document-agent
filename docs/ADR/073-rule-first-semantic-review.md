# ADR-073: 규칙 우선 의미 경로와 조건부 LLM 검토

- 날짜: 2026-11-05
- 상태: 구현 및 선택 회귀 테스트 완료. 실제 LLM 응답 검증은 DNS 장애로 미완료.
- 범위: 동결된 PhysicalTableIR의 행·열 경로 후보 생성 및 검토.

## 결정

1. `tools/semantic_candidates.py`를 HTML 비교 서비스와 검토 팀이 공유한다. 현재 셀의 계층과 페이지 연속성 규칙을 사용하며 모든 경로에 원본 셀 ID를 보존한다.
2. `validation/semantic_gate.py`가 셀마다 `rule`, `llm`, `blocked`를 결정한다. 양축 경로·원문·물리 근거가 있고 충돌이 없으면 규칙 후보를 유지한다. 경로 누락, 빈 원문, 역할 모호성, 물리 불확실성, 경로 충돌, 사용자 이견은 LLM 사유다. 원본 ID 소실, 스키마 한도 초과, 보류한 분리 헤더는 차단한다.
3. 경로 유지와 정답 확정을 구분한다. `semantic_verified=false`, `training_ready=false`를 유지한다. 페이지 연속성으로 얻은 경로의 역할은 임의 확정하지 않으며 `role=unknown`을 보존할 수 있다.
4. LLM 대상만 최대 12개씩 나눈다. 역할까지 모호하면 역할 분류 → 관계 분석 → 별도 검수, 경로만 불확실하면 관계 분석 → 별도 검수를 실행한다. 분류 결과는 관계 분석·검수 입력에 주입하지 않는다.
5. 필요한 원본 셀과 주변 문맥을 포함한다. 근거·입력·관계 후보 한도를 넘으면 묶음을 더 나누고, 단일 셀도 근거를 충분히 담을 수 없으면 차단 기록을 남긴다. 첫 12개 이후 셀을 누락하지 않는다.
6. 원본 ID, 인용, 후보별 검수 누락을 기존 평가기로 검증한다. 오류 경로는 채택하지 않는다. 전송 실패나 예산 소진 이후에는 추가 호출을 중단하고 남은 대상의 미완료 상태를 기록한다. 취소 신호는 다시 전달한다.
7. 규칙만으로 처리하면 API 키 조회·모델 예산 예약·모델 및 원격 tracing 초기화를 하지 않는다. 실제 호출은 기존 일일 ledger를 사용한다. 무제한 예산이나 사용량 초기화는 추가하지 않는다.

## 파일 구성

| 파일 | 책임 |
|---|---|
| `tools/semantic_candidates.py` | 규칙 후보·근거 셀·불확실성 생성 |
| `validation/semantic_gate.py` | 셀별 분기와 사유 |
| `services/semantic_path_review.py` | 동일 후보를 사용하는 원본/저장/후보 비교 |
| `tools/focused_evidence.py` | 대상 묶음 및 앞 페이지 경로 근거 수집 |
| `teams/table_review/graph.py`, `state.py`, `version.py` | 조건부 그래프, 묶음별 에이전트 실행, 전체 대상 처리 기록 |
| `teams/table_review/service.py` | 기존 큐 어댑터, 원본·캐시 검증, 호출·tracing 지연 초기화 |
| `validation/relations.py` | 역할 분류를 생략해도 경로·인용·검수 평가 |
| `runtime/review_queue.py` | 새 실행 버전과 기존 유료 실행 중복 방지 |
| `teams/campaign/pilot.py` | 3회 호출 조건을 셀별 완료 조건으로 대체; 미완료의 명시적 재시도 |
| `web/static/monitor_model.js`, `monitor.js`, `monitor.html` | 규칙/LLM/미완료 수, 분기 사유, 생략 단계 표시 |
| `web/static/campaign.js` | 규칙 처리 완료와 실제 모델 검토 완료 구분 |

## 기록 및 호환성

실행 폴더: `work/document-explorer/review-queue/roles-loop/<실행 ID>/`.

- `request.json`: 원본 snapshot 및 사용자 의견.
- `semantic-plan.json`: 버전, 요청 digest, 모든 후보·분기 사유.
- `batch-000/` 등: focused/relation evidence, scope audit, 원본 crop, 실제 호출 기록, 검증 결과.
- `result.json`, `queue-result.json`: 모든 선택 셀의 outcome, 호출·비용·검증 결과.

완료 여부는 선택 셀의 정확한 집합과 처리 상태로 검사한다. 규칙 완료를 모델 성공으로 집계하지 않는다. 응답이 있어도 검증에 보류가 있으면 `review_complete=false`다. 자동 처리 완료와 마지막 사람 확인은 별개다.

SQLite 컬럼 변경은 없으며 기존 JSON payload/result에 필드를 추가했다. 버전 변경만으로 이미 처리한 basket을 재호출하지 않는다. 기존 실행 폴더에 새 버전을 덮어쓰지 않는다. 전체 작업 DB migration·lease/outbox 통합은 이번 범위 밖이다.

## 실제 동결 사례

LH 광명 국민임대 31쪽, 기존 요청 `908f7a9d03cb4bd28fbe4fb9a24f4338`의 원본을 사용했다.

| 실행 | 결과 |
|---|---|
| 전체 43개 셀 분기 | 규칙 유지 41개, LLM 대상 2개 |
| 농업소득 설명 셀 단독 | 규칙 경로 생성 완료, API 0회 |
| 경로 예시 | 행 `소득 → 사업소득 → 농업소득`, 열 `소득 및 재산 항목 설명` |
| 실제 모델 실행 | 관계 분석 1회 전송 시도, `URLError / DNS_RESOLUTION_FAILED`, 응답 없음 |
| 실패 이후 | 41개 규칙 후보 보존, 2개 미완료, 추가 호출 중단 |
| 원본 보존 | PDF와 document.json SHA-256 동일 |

증거: `reports/semantic-gate-20261105/live-summary.json`, `rule-cell-summary.json`. 실제 실행 폴더 ID는 `semantic-gate-real-lh31-20261105-01`, `semantic-gate-rule-lh31-cell13-20261105-01`이다.

새 Python 코드의 service를 직접 실행했다. 기존 큐 요청을 완료 처리하거나 사용자 의견을 저장하지 않았다. 현재 8766 서버에는 Python 변경이 자동 반영되지 않는다. 서버 프로세스를 안전하게 식별하지 못한 환경이므로 임의 종료하지 않았다. 기존 서버를 정상 종료한 뒤 `.venv/Scripts/python.exe -m doc_understanding.web`로 재시작해야 이후 새 요청이 새 그래프를 사용한다. 기존 요청 화면을 새 실행 결과로 덮어쓰지 않는다.

## 검증과 한계

- Python 선택 회귀 테스트: **78 passed, 5 deselected**. 명령은 plan.md에 기록했다.
- 테스트의 주입 응답은 분기·누락 방지·검증 경계 확인용이며 실제 모델 성공 증거가 아니다.
- Windows TestClient/socketpair 문제로 기존 HTTP API 테스트 5개를 제외했다. 직접 큐·서비스·그래프 및 화면 모델 검증과 구분한다.
- 기존 모니터의 로딩과 동결 사례 표시를 확인했다. 새 분기 결과 패널의 실제 브라우저 검증은 새 서버/큐 실행 후 필요하다.
- 정답 reference 기반 정확도, 잘못된 규칙 유지율, 회귀 점수는 아직 측정하지 않았다. 41/43은 처리 분류이며 정확도가 아니다.
- LH 12쪽 분리 헤더는 계속 보류한다. 물리 파서 변경, 후보 승격, 금융 사실 게시가 없다.
