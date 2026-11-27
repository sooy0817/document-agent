# LangGraph + Langfuse 실행 및 검증 — 2026-10-22

> **2026-11-13 갱신:** 이 문서가 설명하는 그래프(`rag/graph.py`, 옛 `document_rag/graph.py`)와
> `scripts/run_langgraph_rag.py`는 화면·평가·CLI 어디에서도 쓰이지 않아 삭제했다. 아래 구조 그림과
> `run_langgraph_rag.py` 명령은 당시 기록이며 지금은 실행되지 않는다. 지금 질문 흐름의 그래프는
> `rag/agentic/graph.py`이고, 설명은 `docs/RAG_RUNTIME.md`의 "질문 에이전트 그래프"에 있다.
> Langfuse 연결 확인·추적 조회 명령은 그대로 쓴다.

기존 CanonicalDocument/DocumentIR/PhysicalTableIR/StructuredTableIR 계약과 검색 SQL은
변경하지 않았다. 검색 엔진, 표 해석기, 답변 생성기를 새로 만들지 않았다.
Phase 1–3의 identity 경로에서 동등성을 실제 확인한 후 Agent 분기를 추가했다.

## 실제 구조와 재사용 범위

`document_rag/graph.py`가 실제 StateGraph를 컴파일한다.

```text
START → understand_question → retrieve → resolve_evidence → validate_evidence → generate_answer → END
                                          ↑                       │
                                          └─ retrieve ← retry_original_question
```

위 재검색 분기는 `--agent`에서만 활성화되며 최대 1회다. 오류가 발생한 단계는 END로 종료한다.
실제 컴파일 결과는 [identity](../reports/langgraph-identity.mmd),
[Agent](../reports/langgraph-agent.mmd)에 있다.

| 단계 | 실제 실행 함수 |
|---|---|
| understand_question | 기본은 질문 그대로 전달. Agent만 QuestionPlan 분석 |
| retrieve | 기존 `repository.search`, FTS + 부분문자열 SQL 그대로 |
| resolve_evidence | 기존 CLI의 동일 루프를 `service.resolve_evidence`로 이동. 문서 3개, 문서별 셀 8개, 본문 문맥 연결 유지 |
| validate_evidence | 조회 가능 여부, 요청 기간/기관/지표의 근거 존재 확인. 의미 승인과 구분 |
| generate_answer | 기존 `answer.generate`, `check_selection`, `render_answer` 재사용 |
| SQL 최종 확인 | 선택된 셀을 기존 `sql_value_probe`로 JOIN 재조회, 원문 값/행 노드/열 노드 일치 확인 |

State는 원본 질문, 검색 요청, 순위가 있는 후보, 근거, 검증 상태, 답변 결과,
오류, 질문 분석과 재검색 기록을 보존한다. 원문 Document 객체나 인증 설정은 State에 넣지 않는다.
기존 CLI도 동일 서비스 함수를 사용하므로 구현이 갈라지지 않는다.

## 사용자의 프로젝트 연결: 실제 완료

현재 저장한 설정은 `infra/langfuse/client.env.local`이다. 이 파일은 Git 제외 대상이며
키 값은 보고서·채팅·Trace에 출력하지 않는다.

2026-10-22 검색 비교 도중 사용자가 키를 변경했다. 현재 인증 프로젝트는 **pdf_agent**다.
`--langfuse-config`로 선택한 파일을 직접 읽으므로 루트 `.env`나 프로세스 환경변수보다
이 파일이 실제 목적지를 결정한다. 저장 후 새 실행에 적용되며 이미 실행 중인 클라이언트는
자동 재설정되지 않는다. 아래 명령은 키를 출력하지 않고 연결 대상을 확인한다.

```powershell
.venv/Scripts/python.exe scripts/check_langfuse_project.py --config infra/langfuse/client.env.local --expected-project pdf_agent
```

- [현재 pdf_agent 프로젝트](http://localhost:3000/project/cmvj2w2jl0005o7071eiflpzn)
- [현재 프로젝트의 실제 GPT-4.1 E2E](http://localhost:3000/project/cmvj2w2jl0005o7071eiflpzn/traces/e2e83c41a4196fb97c5e7a6d1b744316)
- [검색 비교·Tree Span·비용 상세](../reports/TABLE_RETRIEVAL_COMPARISON.md)

다음은 키 변경 전 `graph` 프로젝트의 이전 LangGraph 검증 기록이다. 이동·삭제하지 않았다.

- [이전 graph 프로젝트](http://localhost:3000/project/cmr8temes0006o708sm29m6sy)
- [2025년 정상 답변 Trace](http://localhost:3000/project/cmr8temes0006o708sm29m6sy/traces/332fa7d14edb210480560303cace1d66)
- [2026년 재검색·보류 Trace](http://localhost:3000/project/cmr8temes0006o708sm29m6sy/traces/5e6b5806342d47520d2da6f54819e364)

인증뿐 아니라 서버 저장 후 재조회까지 완료했다. 정상 답변은 관측 24개, 보류는 43개다.
각 실행은 물리적 root가 하나이며 모든 관측의 traceId가 동일하다.
기존 사용자 서버는 `langfuse/langfuse:3`이다. 최신 Observations v2 조회가 404를 반환하여
기존 공개 REST `GET /api/public/traces/{id}`로 읽기 전용 호환 조회를 구현했다.
서버·기존 데이터·Docker 설정은 변경하지 않았다. 최신 SDK의 공식 v4 권장 환경도 아래 별도로 유지한다.

대시보드에서 `nh-table-rag → LangGraph`를 펼치고 다음 관측을 확인한다.

| 관측 | 실제 입력·출력 내용 |
|---|---|
| understand_question | 원본 질문, 의도/기관/기간/지표 후보, 재작성 검색어, 추정 출처, 근거로 관측된 기간 |
| gpt-4.1.understanding | 분석 결과, 실제 토큰/추정 비용 |
| postgres.search | 실제 검색어, 토큰, 점수 공식, Top-8의 순위·점수·run/document/table/entry ID |
| table.resolve_cells | 셀 ID/원문 값/행·열 경로/단위/기간 머리말/주석/출처/후보 상태 |
| validate_evidence | 충분 여부와 사유. `sufficient`는 의미 검증 완료를 뜻하지 않음 |
| retry_original_question | 재검색 이유, 원본 검색어, attempt=1 |
| gpt-4.1.answer | 최소 근거 선택, 실제 응답 토큰/비용, 최종 답변 및 선택 근거 |
| postgres.cell_lookup | 실제 SQL 값 및 행·열 노드 ID |
| generate_answer / root | 최종 답변·선택·오류 또는 보류 사유 |

`RunnableCallable`은 공식 Callback이 기록한 조건부 분기 관측이다. 추가 GPT 호출이나
별도 Trace가 아니다. SDK root를 하나만 만들고 공식 Callback이 그 아래 그래프/노드를 만들며,
기존 검색/문서 조회/셀 조회와 GPT 관측은 현재 노드 아래에 중첩한다.
OpenAI/LangChain 모델 자동 계측을 함께 켜지 않아 중복 generation을 만들지 않는다.

## 실제 결과 비교

질문: `강동농협 재무현황에서 2025년 현금 금액은 얼마인가요?`

| 비교 항목 | 기존 CLI와 동일 서비스 경로 | LangGraph identity |
|---|---|---|
| 후보 순위·점수·ID | 실제 PostgreSQL Top-8 | 전체 동일 |
| 검색 1위 | p.2 `physical000`, score 0.5809523809523809 | 동일 |
| 문서 | `sha256:4e251561e61e01ecd45f71507719bf4f3a0e47c79135444689e30b5bcbbb8145` | 동일 |
| 표 | 위 문서 `/p0002/physical000` | 동일 |
| 선택 셀 | 위 표 `/cell0018` | 동일 |
| 행 경로 원문 | `자\n산 → Ⅰ.유동자산 → 1.현금` | 동일 |
| 열 경로 원문 | `2025년(B) → 금액` | 동일 |
| 원문 값 | `963` | 동일 |
| 단위 후보 | `(단위 : 백만원, %)` | 동일 |
| 주석 | 이 셀의 연결 주석 없음 | 동일 |
| 의미 상태 | `unvalidated_candidate` | 동일 |
| 출처 | 강동농협 요약공시 PDF p.2 + 표/셀 ID | 동일 |
| 최종 선택·답변 | E1 / 963, 미검증 경고와 출처 | 문자열까지 동일 |

위 비교는 독립 실제 GPT 호출 두 번의 이번 결과이며 향후 확률적 응답의 영구 동일성을
보장한다는 뜻이 아니다. 순위·근거·렌더링의 회귀는 Mock으로도 고정했다.
실행 결과는 `data/langgraph-rag/phase13-2025/comparison.json`과 `sql-probes.json`에 있다.

`2026년`으로 바꾼 질문도 identity 두 경로에서 후보/근거/선택/답변이 동일했고 빈 선택으로 보류했다.
Agent에서는 요청 기간이 열 경로에 없어 `REQUESTED_PERIOD_NOT_IN_COLUMNS`를 남기고
원본 질문으로 1회 재검색한 뒤 검증 단계에서 보류했다. 이때 답변 GPT 호출은 0회다.
질문 분석 GPT 호출 1회는 별도로 기록된다. 2025년 Agent는 재검색 없이 동일 셀을 선택했다.

질문 분석 결과는 `llm_hypothesis`다. 원문에 없는 기관·기간·지표를 추측하면 원문 검색으로
즉시 복귀한다. 추측 조건을 SQL 필터로 사용하지 않는다. caller의 run_id만 유지한다.
`verified_conditions.periods`는 실제 열 라벨에서 관측한 기간이며 의미/재무 사실 승인과 다르다.
잘못된 재작성 검색의 빈 결과 → 원본 검색 복귀, 분석 스키마 오류 → 원본 복귀,
없는 기간 → 재검색 1회 제한을 Mock 회귀로 검증했다.

## 실행 명령

작업 디렉터리는 프로젝트 루트다. 실제 GPT 호출은 아래 `--allow-external`을 명시한 별도 실행에서만 한다.
출력 폴더는 매번 새 경로를 지정한다. 기존 출력은 덮어쓰지 않는다.

```powershell
.venv/Scripts/python.exe -m pip install -e ".[graph]"

# 설정은 편집 탭에서 저장. 값은 출력하지 않는다.
# infra/langfuse/client.env.local: LANGFUSE_BASE_URL, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY
.venv/Scripts/python.exe scripts/check_langfuse_connection.py --config infra/langfuse/client.env.local --output data/langgraph-rag/new-connection.json

# Phase 1–3, 원본 질문 그대로. 기존 경로 + graph에서 각각 GPT 1회.
.venv/Scripts/python.exe scripts/run_langgraph_rag.py --question "강동농협 재무현황에서 2025년 현금 금액은 얼마인가요?" --output data/langgraph-rag/new-identity --langfuse-config infra/langfuse/client.env.local --compare-legacy --allow-external

# Phase 4: 분석 최대 1회 + 답변 최대 1회, 검색 최대 2회.
.venv/Scripts/python.exe scripts/run_langgraph_rag.py --question "강동농협 재무현황에서 2025년 현금 금액은 얼마인가요?" --output data/langgraph-rag/new-agent --langfuse-config infra/langfuse/client.env.local --agent --allow-external

# <TRACE_ID>를 summary.json의 실제 ID로 치환. API 조회는 비동기 반영 지연 가능.
.venv/Scripts/python.exe scripts/read_langfuse_trace.py --config infra/langfuse/client.env.local --trace-id <TRACE_ID> --output data/langgraph-rag/new-observations.json
```

`--langfuse-config`를 생략하면 관측 없이 RAG가 실행된다. JSON 설정도 계속 지원한다.
로컬 외 목적지는 사용자가 명시한 설정 파일의 HTTPS 주소만 허용하며 자동 Cloud 폴백은 없다.
기존 사용자 서버의 HTTP localhost도 지원한다.

별도 최신 로컬 서버는 프로젝트 `nh-rag-observability`, UI `http://localhost:3008`이다.
테스트한 버전은 LangGraph 1.2.16 / Langfuse SDK 5.0.4 / langchain-core 1.6.13 / 서버 4.67.0이다.
기존 NH PostgreSQL과 서버 3000의 DB/네트워크/볼륨을 공유하지 않는다.

```powershell
# 새 환경에서만 1회. 이미 만들어진 자격 증명은 덮어쓰지 않는다.
.venv/Scripts/python.exe scripts/init_local_langfuse.py
docker compose --env-file infra/langfuse/.env.local -f infra/langfuse/compose.yaml up -d
docker compose --env-file infra/langfuse/.env.local -f infra/langfuse/compose.yaml ps
```

별도 서버 로그인 정보는 `infra/langfuse/access.local.txt`에만 있고 채팅에 출력하지 않았다.
기존 사용자 프로젝트를 연결한 뒤에도 3008 서버는 비교 실행 기록 보존을 위해 유지한다.

## 전송 제한과 장애 처리

공식 SDK export-stage masking으로 콜백 State도 허용 필드만 추린다. 문서 객체, PDF 전체,
SQL, 헤더, 설정, 파일 경로, 인증 값은 전송 대상에서 제외한다. 근거 문자열/리스트/중첩 깊이에
상한을 적용하고 이메일·전화·식별번호·키 형태를 가린다. 실제 출처 식별자와 선택 값은 보존한다.
OTEL exporter의 마지막 단계에서 SDK scope attributes/이벤트/링크를 제거해 공개 키도 제외한다.
실제 사용자 프로젝트의 public/secret key 및 OpenAI 키 부재를 재조회 결과에서 검사했다.

초기 4개 개발 Trace에는 SDK가 자동 추가한 **공개 프로젝트 식별자**가 scope metadata에 있었다.
비밀키는 포함되지 않았다. 이를 발견해 최종 3008 Trace 2개와 사용자 프로젝트 Trace 2개에는
해당 식별자도 제거했다. 이 이력을 숨기거나 초기 검사를 전부 통과한 것으로 보고하지 않는다.

관측 기능 초기화/Span 갱신/종료 실패는 RAG를 다시 실행하거나 중단시키지 않는다.
그래프 실행은 flush를 기다리지 않으며 CLI가 답변 뒤 별도로 flush한다.
HTTP timeout은 2초이고 SDK 백그라운드 재시도 실패는 로그로 남을 수 있다.
없는 localhost:3999에 연결한 실제 SDK 실행에서도 동일 답변 생성과 SQL 일치를 확인했다.
이 장애 시험은 저장 응답 replay로 GPT 호출/비용 0이었다. 임의 Trace ID 생성은 전달 성공 증거가 아니다.

## 비용·테스트·남은 한계

이번 작업 전체 실제 GPT-4.1 호출은 **13회**, 추정 **$0.105736**이다.
사용자 프로젝트로 옮겨 확인한 마지막 두 질문은 3회 / $0.017396이다.
연결 확인과 장애 replay는 OpenAI 호출 0회다. 이전 작업의 비용은 합산하지 않았다.
실제 응답 usage와 호출별 경로는 [기계 판독 결과](../reports/langgraph-langfuse-results.json)에 있다.
입력 $2/M, 출력 $8/M, 캐시 할인 제외 추정이며 청구 금액이 아니다.
[공식 가격](https://developers.openai.com/api/docs/pricing)을 대조했다.

신규 LangGraph/질문 분석/관측/설정/호환 조회 회귀는 **13 passed**다.
일반 pytest는 테스트 키 주입/외부 socket 차단/모델 Mock/LangSmith 비활성화를 유지한다.
로컬 PostgreSQL 테스트도 GPT는 Mock이다. 최종 신규 회귀 결과는
`reports/langgraph-regression-final.xml`, 전체 실행 이력은 `reports/langgraph-tests-complete.xml`에 있다.
최근 전체 실행은 **354 passed / 1 failed / 12 subtests**였고, 실패는 기존 Metric UI TestClient의
Windows `socketpair → listen` WinError 10014였다. 해당 API/Metric/RAG 묶음의 별도 실행은
30 passed / 1 PostgreSQL opt-in skipped였다. 이 환경 오류는 아직 원인 해결로 표시하지 않는다.
전체 실행 이후 추가된 관측/설정/호환 조회 검증은 신규 회귀에 포함된다.

두 개발 질문에서 검색·선택·출처의 동등성과 실제 SQL 일치를 확인했다. 이를 500건 검색 정확도,
모든 Row/Column 의미 정확도, 단위·주석 적용 범위의 사람 검증 완료로 확대 해석하지 않는다.
IR/문서 구조의 미검증 상태와 사실 발행 0건을 유지한다. 기존 25건 적재를 이번 변경에서 재적재하지 않았다.

공식 구현 근거: [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api),
[Langfuse LangGraph 연동](https://langfuse.com/integrations/frameworks/langgraph),
[SDK v5 변경](https://langfuse.com/docs/observability/sdk/upgrade-path/python-v4-to-v5),
[export masking](https://langfuse.com/docs/observability/features/masking),
[공식 Docker 구성](https://langfuse.com/self-hosting/deployment/docker-compose).
