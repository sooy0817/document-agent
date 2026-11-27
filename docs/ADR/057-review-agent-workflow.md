# ADR-057: 오류 담기 큐의 제한된 모델 분석·검수

2026-11-03. 승인된 범위: 자동 검토, 화면 보고. 파서/원본 IR/Gold 자동 변경은 금지.

## 실행

기존 SQLite 큐에 local/agents 실행 방식을 추가한다. 기존 요청은 local로 유지한다.
새 모드는 별도 버전 해시로 요청을 만들고 기존 완료 결과를 덮어쓰지 않는다.
자동 모델 검토를 켠 이후 저장한 피드백은 agents로 등록한다. 과거 사례는 개별 실행한다.
상태가 꺼지면 모델 호출을 시작하지 않는다. 진행 중 HTTP 호출의 실제 취소를 보장하지는
않으며, 큐 취소 시 이후 검수 호출과 최종 결과 반영을 막고 이미 발생한 호출 기록은 보존한다.

LangGraph StateGraph:
`collect_evidence → analyze → judge → validate_review → report`.
각 노드는 진행 상태를 기록한다. 분석/검수는 GPT-4.1의 서로 다른 프롬프트·호출이며
독립 모델이나 사람 검수가 아니다. Judge는 원본과 분석 후보를 모두 받고 반대할 수 있다.
재계획·자동 코드 수정·데이터 자동 수집·자동 학습은 포함하지 않는다.

## 재사용

기존 context_evidence로 부모/자식·계속표 참조를 모으고 CanonicalDocument의 실제 셀,
Tree 경로, 원문 span을 읽는다. 기존 Responses http_transport와 Trace의 마스킹·장애 격리를
재사용한다. 두 에이전트의 결과는 별도 검토 계약이며 기존 문서 계약은 변하지 않는다.

선택 페이지, 부모 연결 자료, 인접 페이지 상하단을 수집한다. 전체 문서 검증으로 표시하지
않는다. 셀/텍스트 수와 요청 바이트를 제한하며 제외 항목은 명시한다. 사용자 의견과
문서 내용은 명령이나 Gold가 아니다. 정답 참조 이력은 모델 입력에 넣지 않는다.

## 검증·보류

Pydantic 스키마, 실재하는 ID, 원문 인용, 자기참조/관계 순환, 표를 넘은 행/열 연결,
Judge의 모든 issue 검수 여부를 검사한다. 원본 변경, 잘못된 ID/인용, 모델 실패,
근거 누락 또는 분석·검수 불일치는 보류한다. 코드 통과도 의미 승인이 아니다.
항상 semantic_verified=false, training_ready=false, human_review_pending.

## 호출·복구

요청당 분석 1회 + 검수 1회, 한국 시각 하루 최대 20회 예약을 별도 SQLite에 기록한다.
HTTP 호출 전에 예약과 started 파일을 저장하고, 응답/usage/비용/검증 결과를 요청별로
보존한다. 재시작 시 저장된 응답을 재사용한다. 호출 중 중단되어 응답이 없으면 비용 미확정으로
보류하며 자동 재과금하지 않는다. 예산 초과도 자동 재시도하지 않는다.
동일 요청의 수동 재시도는 성공/실패 호출 기록을 재사용한다. 새 모델 실행은 새 피드백 버전으로
분리한다. 단일 서버 프로세스 범위이며 분산 워커는 지원하지 않는다.

## 관측

요청당 root span 1개에 수집/분석/검수/코드검증 span을 붙인다. 별도 LangGraph callback을
중복 등록하지 않는다. 이미지·PDF·전체 JSON·API 키는 Langfuse로 전송하지 않는다.
제한된 요약·ID·usage·추정 비용만 기록한다. 로컬 evidence/모델 응답과 trace_id를 화면에서
연결한다. Langfuse 장애는 검토 실행을 막지 않는다.

## 공식 참고

- https://docs.langchain.com/oss/python/langgraph/graph-api
- https://developers.openai.com/api/docs/models/gpt-4.1

검증 결과와 실제 호출 비용은 reports/review-agents-20261103에 별도 기록한다.
