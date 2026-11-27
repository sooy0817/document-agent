# ADR-047 — 원본 근거를 사용하는 평가 전용 Axis Judge

2026-10-29. 기존 생성·검사·StructuredTableIR·Canonical·RAG·DB 계약은 변경하지 않는다.

## 문제와 결정

ADR-046 iM 내부 수수료표의 경로 4개는 물리 ID/기하 검사를 통과했지만 바깥 `내용`을
내부 열 제목으로 사용하고 명시된 상위 행 라벨을 누락했다. 기하 일관성만으로 의미를
승인할 수 없으므로 기존 저장 출력을 고정하고 별도 GPT-4.1 Judge로 평가한다.

입력은 원본 영역 이미지, 셀 ID/원문/좌표/span/병합, 판정할 단일 경로 및 역할이다.
Gold, 생성 이유/신뢰도, 이전 검사 결과, 문서명 기반 정답은 모델에 제공하지 않는다.
기존 생성 모델은 GPT-4o-mini이며 Judge는 GPT-4.1-2025-04-14이다. 모델 분리는
오류 독립성을 보장하지 않으므로 개발 사례 보정과 사람의 원본 검토가 계속 필요하다.

## 평가 계약

`role`, `row_path`, `column_path`, `local_scope` 각각 0(오류), 1(불완전), 2(근거 일치),
null(판단 불가). 0이 있으면 incorrect, 아니면 null→unjudgeable, 1→partial, 전부 2→correct.
근거 cell ID와 짧은 이유를 요구한다. 모델 출력의 ID·점수 일관성 검사와 의미 정답은 별개다.
Judge는 경로 수정·승인·학습 데이터 승인·재생성 재시도를 하지 않는다.

LangGraph: prepare_candidate → judge_llm → validate_judgement. 기존 그래프와 별도이며
영역당 단일 대상 평가를 최대 1회 호출한다. 명시적 실행만 API를 허용하며 일반 pytest는 mock이다.
대량 큐·독립 holdout·RAG 통합·학습은 이번 범위 밖이다.

## 관측과 보존

사례당 root Trace 1개. 직접 span만 사용하며 callback 중복 생성은 없다.
source_candidate observation에 rubric 점수와 verdict를 연결한다. 결정적 검사와 개발 참조 일치는
별도 score다. source_candidate / deterministic_check / judge_structure / validate_judgement /
record_evaluation 관측을 남긴다. 전체 PDF·이미지 base64·키·전체 모델 요청은 trace로 보내지 않는다.
원문 일부, 경로, cell ID, 근거, 토큰·비용만 기존 allowlist로 보낸다. 로컬에는 재현용 전체 응답을 보존한다.

SDK5의 score batch API가 404 또는 명시적인 root object/array 형식 충돌 400이면
사용자의 기존 로컬 v3 서버 public scores REST를 사용한다. 임의의 400은 fallback하지 않는다.
trace와 score ID/값/설명/observation 연결을 재조회해야 저장 성공이다. 전송 실패는 평가를 중단하거나
유료 모델 재호출을 유발하지 않는다. unknown/실패는 분모에 유지한다.

## 재현

`.venv/Scripts/python.exe scripts/run_axis_judge.py --manifest reports/axis-judge-20261029/cases.json --output reports/axis-judge-20261029/run-01 --allow-external --max-calls 5 --env-file .env --langfuse-config infra/langfuse/client.env.local`

출력 디렉터리는 새 경로만 허용한다. 코드·프롬프트·원본 해시를 API 실행 전에 동결한다.
사례 5개는 기존 실패 4개와 정상 1개를 의도적으로 고른 개발 보정 자료이며 일반화 정확도가 아니다.

## 실제 결과 및 제한

`reports/axis-judge-20261029/run-01`에 모델 5회 원본 실행이 있다. iM 4개는 incorrect,
하나은행 1개는 correct로 기존 참조 판정과 일치했다. 그러나 iM의 빠진 상위 행 라벨을
Judge가 놓쳐 행 경로는 모두 2점을 주었다. 행 경로 완전성 평가 일치는 1/5,
열 경로 정확성 평가 일치는 5/5다. 영역 범위 점수는 별도 독립 정답 점수로 집계하지 않았다.
개발 사례의 최종 verdict 5/5를 Judge 일반화 정확도 또는 전체 구조 정확도로 표현하지 않는다.

초기 score API는 SDK5 array / 기존 서버 object 충돌로 400이 발생했다.
`export-recovery-01`은 같은 Trace와 score ID에 재전송하고 5개 Trace·35개 score의 값/근거/연결을
재조회한 결과다. 원본 실행은 보존하고 모델 재호출은 하지 않았다.
`source-review.json`의 기존 원본 참조 비교는 Judge와 분리하여 `axis_reference.*_path_exact`
점수로 추가할 수 있다. Judge의 오채점을 덮어쓰지 않는다.

실제 모델 5회 비용은 캐시 입력을 반영하여 $0.063116 추정이며 사용량은 각 사례 JSON에 있다.
Judge 모델·프롬프트를 결과에 맞춰 재작성하거나 이번 5건에 재실행하지 않았다.

최종 원본 참조 score 10개까지 포함한 45개 score를 실제 재조회했다. 로컬 서버의 GET scores가
traceId 필터를 무시하는 경우에 대비해 로컬 필터·페이지 조회를 적용했다. 관련 회귀 73개 통과;
API 응답 mock 및 기존 네트워크 차단 fixture를 유지했다. 최종 요약은
`reports/axis-judge-20261029/comparison-reviewed.html`과 `verification.json`에 있다.
