# 프로젝트 코드와 자료 위치

현재 목적과 계층 경계는 [DESIGN.md](DESIGN.md), 실행 명령은
[DEVELOPMENT.md](DEVELOPMENT.md)를 따른다. 이 문서는 현재 파일 위치를 안내한다.

## 최상위 폴더 안내

```text
doc_understanding/
├─ parser/       원본 추출·표 복원·행열 구조·문서 조립
├─ agents/       모델 역할·팀 흐름·검토 계약과 후보 보완
├─ rag/          검색 항목·검색·근거 구성·답변
├─ tools/        검토할 원본 근거 선택·확장·관계 조회
├─ storage/      원본 산출물·IR·실행별 저장과 DB 전송
├─ domain/       공통 IR·문서 계약·원문 숫자 분류
├─ runtime/      모델 연결·예산·큐·추적·공통 동결 검사
├─ validation/   출처·관계·모델 응답 검사
├─ review/       사람 검토·의미 설명·오류 바구니
├─ evaluation/   저장 결과의 정답 비교·회귀 비교·평가 기록
├─ experiments/  수정 제안·격리 실행·복원 후보
├─ services/     문서 처리·표시·비교 서비스
├─ api/          저수준 HTTP 추출 API
└─ web/          검토 화면과 HTTP 진입점
```

파서의 기본 진입점은 `parser/pipeline.py`, RAG CLI는 `python -m doc_understanding.rag`다.
`storage/documents.py`는 원본·문서 검증과 저장 준비·전송을 담당한다.
`rag/repository.py`는 이 저장 서비스를 호출하고 검색 인덱스 생성과 재조회 검증을 연결한다.
저장 전송과 DDL은 `storage/staging/`에 있으며 DB 스키마와 기존 적재 진입점은 유지한다.
`domain/`의 공통 계약과 `agents/contracts/`의 검토 계약은 서로 다른 목적을 가진다.

## 현재 표 이해 코드 배치

`src/doc_understanding/parser/`는 물리·구조·관계·문맥 패키지로 나눈다.
각 Python 파일의 상단 한글 주석에서 역할과 원본 보존 경계를 확인할 수 있다.

| 폴더 | 책임과 주요 진입점 |
|---|---|
| `physical/` | 선·표 영역·격자·셀·병합·중첩 복원. `physical_grid.py`가 연결하고 `physical_models.py`가 계약·설정을 정의한다. |
| `structure/` | 한 표 내부의 역할·행열 경로 후보. `analyzer.py`가 흐름을 연결하고 `trees.py`, `validation.py`, `models.py`가 트리·검증·계약을 담당한다. |
| `relations/` | `logical_tables.py`의 표 조각 연결 후보와 `continuation_graph.py`의 선택적 모델 검토. |
| `../agents/axis_review/` | `axis_*`의 선택적 행열 후보 생성·검토·평가. 기본 추출 단계에서 전부 실행되는 것은 아니다. |
| `context/` | `period_*`의 선택적 기간 정의·적용 범위 검토. 원본 IR과 별도로 결과를 만든다. |
| 루트 `contracts.py` | 기존 공용 프로토콜과 초안 계약. 실제 의미 후보 모델은 `structure/models.py`를 사용한다. |

기본 PDF 실행은 `services/documents.py` → `parser/pipeline.py`에서 시작한다.
`parser/physical/runner.py`가 물리 추출을 실행하고 `structure/analyzer.py`가 구조 후보를 만든다.
`parser/assembly/document.py`가 문서 단위로 조립하며 RAG 검색·답변 코드는 계속
`rag/`가 담당한다. `parser/physical/fill_boundaries.py`는 이름과 달리
현재 기본 물리 처리 경로에서 사용되므로 보존한다.

이동 전 `table_understanding/semantic.py`는 `structure/analyzer.py`와 트리·검증 파일로,
`semantic_models.py`는 `structure/models.py`로 분리했다. 이전 평면 import는
소스·테스트·실행 스크립트에서 새 경로로 변경했으며 구경로 호환 복사본은 두지 않는다.
과거 ADR·실험 보고서의 경로는 당시 이력이다. 현재 코드 위치는 이 표를 따른다.
데이터 계약과 알고리즘은 유지하며, 물리 동결 검사 방식은 [PARSER_FREEZE.md](PARSER_FREEZE.md)를 따른다.

## 행·열 경로를 개선할 때 읽는 순서

물리 추출·셀 복원은 기존 동결 기준을 유지하고, 현재 후속 개발은 원본을 참조하는
행·열 계층과 경로 후보에 집중한다. 주석 추가는 동결 해제나 새 정확도 승인을 뜻하지 않는다.

1. `parser/pipeline.py`: 물리 결과를 구조 분석기에 넘기는 전체 실행 흐름.
2. `parser/structure/analyzer.py`: 헤더·행 라벨 경계, 패널, 셀 역할과 값 매핑.
3. `parser/structure/trees.py`: `parent_candidates` → `build_tree` →
   `leaves_for` → `path_for`의 부모 선택과 루트부터의 경로 구성.
4. `parser/structure/models.py`: 노드 ID 경로와 원본 라벨·셀 ID의 구분.
5. `parser/structure/validation.py`: 원문·점유 보존, 순환, 패널 교차, 경로 충돌 검사.
6. `parser/assembly/document.py`: 번호 기반 행 후보 보완과 문서 단위 조립.
   표 사이의 경로는 `parser/relations/logical_tables.py`에서 별도로 확인한다.

`cell_mappings`는 값 셀용이다. 라벨 셀 자신의 조상 경로를 볼 때는 해당 축 트리의
노드에서 출발해야 하며, 같은 글자라는 이유로 다른 패널·부모의 노드를 합치지 않는다.
후보가 여럿이거나 근거가 누락되면 unresolved와 후보 목록을 유지한다.

각 파일 상단은 역할, 함수 앞 한글 주석은 입력·출력과 보류 조건을 설명한다.
엄격한 원본 해시 검사를 받는 두 모델 파일은 기존 파일 상단 설명만 보강했다.
모델 필드·기본값·스키마와 검사 규칙은 변경하지 않는다.

## 실행 코드

| 경로 | 역할 |
|---|---|
| `src/doc_understanding/parser/assembly/` | `document.py`의 문서 조립·번호 기반 행 후보 보정·원본 연결 검증. 서비스와 RAG가 함께 사용한다. |
| `src/doc_understanding/agents/roles/` | 각 역할의 프롬프트·응답 형식·실행 |
| `src/doc_understanding/agents/workflows/` | 팀별 LangGraph, 상태, 서비스 및 모드 연결 |
| `src/doc_understanding/agents/contracts/` | 근거 묶음·제안·검토 snapshot의 계약 |
| `src/doc_understanding/agents/semantic_resolver/` | 원본과 별도로 만드는 의미 보완 후보 |
| `src/doc_understanding/agents/structure_review/` | 적재 시점 구조 검토 에이전트(ADR-078). `layout.py` 헤더 근거 등급·판정, `scope.py` 중첩 표 적용 범위, `selector.py` 보류 판정의 모델 선택·Judge, `review.py` 분석기 재실행·문서 검증으로 검토 후보 문서 생성, `__main__.py` CLI |
| `src/doc_understanding/runtime/structured_call.py` | 에이전트가 공유하는 제한된 구조화 모델 호출(예산·감사 기록·스키마 검증, 재시도 없음) |
| `src/doc_understanding/storage/` | 원본 파일 저장·PostgreSQL 전송·저장 검증 |
| `src/doc_understanding/evaluation/`, `experiments/` | 평가 및 기본 실행과 분리된 실험 |
| `src/doc_understanding/review/` | 사람 검토 앱·의미 설명·오류 바구니 |
| `src/doc_understanding/tools/` | 원본 근거 선택·검색 |
| `src/doc_understanding/validation/` | 원본·관계·응답 검증 |
| `src/doc_understanding/runtime/` | 모델 연결·큐·예산·설정 |
| `src/doc_understanding/services/`, `web/` | 문서 처리와 화면 |
| `src/doc_understanding/experiments/parser_repair/` | 격리된 파서 수정 실험·재추출 |
| `src/doc_understanding/parser/extraction/`, `parser/`, `rag/` | PDF 추출·표 구조·검색 |

기존 `rag/document.py`의 호출부는 `parser/assembly/document.py`로
변경했으며 구경로 모듈은 두지 않는다. `parser/structure/text_grid.py`의 선택적 2열
보완 호출과 `parser/assembly/nested_content.py`의 중첩 분석은 현재 위치에 유지한다.

팀별 실행 순서와 역할은 [AGENT_TEAMS.md](AGENT_TEAMS.md)를 참조한다.
스크립트 목록은 [SCRIPT_INDEX.md](SCRIPT_INDEX.md)를 참조한다.
기존 팀은 정해진 LangGraph 흐름이며 Supervisor가 도구를 자율 선택하는 구조는 아니다.

## 테스트, 자료 및 결과

| 경로 | 분류와 취급 |
|---|---|
| `tests/`, `tests/fixtures/` | 회귀 테스트와 입력 데이터. 임시 폴더가 아니다. |
| `data/` | 원본 자료. 보존한다. |
| `work/` | 실행 상태·검토 이력 등. 임의로 지우지 않는다. |
| `reports/` | 평가·검증 기록. 일부 테스트와 스크립트가 참조한다. |
| `output/`, `outputs/` | 기존 산출물. `tests/test_web.py`가 `outputs/`의 하나은행 자료를 참조하므로 통합 전에 전체 의존성 확인이 필요하다. |
| `github-portfolio/`, `public-release/` | 공개 후보와 검증용 복사본. 준비 스크립트가 둘을 사용하고 테스트도 포트폴리오 데이터를 참조한다. |
| `tmp/pdfs/`, `tmp/resume-review/` | 기존 임시 자료. 출처와 재생성 가능 여부를 확인하기 전까지 보존한다. |
| `tmp/archive/` | 이전 리팩터링의 검증된 ZIP과 중복 원본. 현재 삭제가 차단되어 용량은 줄지 않았다. |
| `.venv/`, `.venv-baseline/` | 독립 Python 환경. 내부 실행 파일은 개별 삭제하지 않는다. |

`.vscode/settings.json`은 가상환경과 `tmp/` 등을 탐색기에서 숨긴다. 파일 자체를 삭제하지 않는다.

## 문서·과거 자료

현재 기준 문서 목록은 [README](../README.md#문서-안내)에 있다.
`docs/ADR/`는 당시 설계 결정, Phase·MVP 문서는 과거 계획·결과다.
현재 코드 위치를 찾을 때 과거 보고서의 평면 경로나 `demo/` 경로를 사용하지 않는다.

변경 파일 목록과 검증 결과는 해당 커밋·PR 또는 실행 보고서에서 확인한다.
과거 코드 복구 정보는
[개발 안내](DEVELOPMENT.md#과거-코드자료를-확인할-때)를 따른다.

## RAG 책임별 패키지

| 위치 | 책임 |
|---|---|
| `rag/indexing/entries.py` | 본문·표 요약·행·긴 셀 검색 항목 연결 |
| `rag/indexing/chunking.py` | 문자 범위 분할과 같은 원본 블록 내 문단 후보 묶음. 파서 IR은 변경하지 않음 |
| `rag/indexing/text.py` | 본문·긴 셀의 검색 텍스트와 원문 ID·문자 범위·출처 연결 생성 |
| `rag/indexing/nested.py` | 중첩 부모 셀의 본문 청킹과 문맥 투영 |
| `rag/indexing/writer.py` | 검색 인덱스 생성·갱신과 해시·버전 충돌 검사 |
| `rag/retrieval/lexical.py` | 한글 부분 문자열 토큰과 키워드 점수 |
| `rag/retrieval/repository.py` | PostgreSQL 검색·원본 재조회·셀 SQL 대조 |
| `rag/embedding/models.py`, `client.py` | 임베딩 프로필·벡터 계약과 외부 API 호출·누적 예산 검사 |
| `rag/embedding/indexer.py`, `repository.py`, `schema.sql` | 변경된 검색 텍스트의 배치 생성·해시 재사용·트랜잭션 저장 |
| `rag/embedding/options.py`, `commands.py` | CLI 모델·단가 설정과 실행·실패 감사 기록 |
| `rag/retrieval/dense.py`, `hybrid.py`, `runtime.py` | 동일 run 범위의 코사인 검색·RRF 결합·적용 커버리지 기록 |
| `rag/evidence/cells.py`, `pack.py`, `nested.py` | 원본 셀·본문·중첩 근거 복원과 한도 적용 |
| `rag/evidence/projections.py` | 검색 조각을 원문에서 재생성한 표현과 대조하여 범위·출처·문구 변조 차단 |
| `rag/evidence/validation.py`, `constraints.py` | 근거 형식·조회 가능 상태·요구 출처 사전 검사 |
| `rag/evidence/assessment.py` | 질문 조건별 모델 판단 계약과 질문·원문 인용·선택 근거 연결 검사 |
| `rag/generation/` | 질문 해석·답변 생성·인용 검사·모델 비용 정책 |
| `rag/prompts/` | 질문 해석(`understanding.py`)·근거 선택(`selection.py`)·서술(`narrative.py`)·공통 질문 충족 판단(`assessment.py`)의 모델 지시문 |
| `rag/observability/` | 실행 추적과 평가 점수 전송·조회 |
| `rag/service.py`, `graph.py` | 실행 단계 연결과 제한된 재검색 |
| `rag/agentic/` | 질문 이해 에이전트(ADR-079). `plan.py` 질문 인용에 고정된 계획, `tools.py` 표 구조 조회·구조 일치, `run.py` 계획-실행 흐름과 1회 재검색. 지시문은 `rag/prompts/planner.py` |
| `rag/evidence/scope.py` | 구조 검토 후보(ADR-078)가 표에 남긴 적용 범위·레이아웃 판정 읽기 |
| `services/corpus_rag.py`, `web/static/ask.*` | 문서 코퍼스(문서별 최신 실행) 목록·일괄 적재·코퍼스 질문과 `/ask` 화면. 웹 RAG 실행기(`services/web_rag.py`)의 검사·준비·질문 단계를 재사용 |
| `runtime/job_queue.py`, `services/batch_ingest.py` | PostgreSQL 작업 표(`jobs.job`)와 일괄 처리 명령(등록·작업자·상태·보고서·재시도). 분석 → 색인(규칙 구조 검토 포함) → 임베딩(로컬) 단계를 큐로 잇는다. 사용법은 `docs/DEVELOPMENT.md` |
| `rag/repository.py` | 원본 저장 → 색인 → 재조회 연결 및 기존 조회 함수 재노출 |
| `rag/index.py` | 기존 검색 항목·근거 함수 import 호환 창구; 구현 중복 없음 |
| `rag/__main__.py`, `batch.py` | 문서 CLI·임베딩 CLI와 기존 문서별 배치·검증 재개. 기존 batch는 lexical 기준선 유지 |
| `rag/schema.sql` | 기존 검색 스키마; 원본 저장 스키마는 `storage/staging/` |

새 구현은 책임별 패키지에서 수정한다. `index.py`와 `repository.py`의 기존 공개 함수는
외부 실행 코드와 저장 서비스 호출을 위해 유지한다. 답변·추적 등 이동한 모듈의 호출부는
새 경로를 사용한다. CLI 명령과 JSON 계약, 검색 점수식·한도·미해결 매핑 정책은 유지한다.
`schema.sql`은 기존 위치와 패키지 데이터 선언을 유지하며 `repository.migrate()`가 읽는다.
`batch.py`도 기존 위치에 두어 subprocess 실행 경로와 코드 지문 수집 범위를 유지한다.
파일 이동·주석도 코드 지문을 바꾸므로 이전 배치 결과는 기존 정책대로 재사용이 제한될 수 있다.
원본 IR·기존 실행·동결 manifest와 KB 산출물은 수정하지 않는다.

`compose.yaml`의 로컬 postgres는 pgvector 지원 PostgreSQL 16 이미지를 사용한다.
`scripts/setup_rag_vector.ps1`은 기존 볼륨을 유지하며 서비스 갱신·확장 설치·연산 확인을 수행한다.
`migrations/versions/028_rag_embeddings.py`는 벡터 DDL의 Alembic 진입점이며,
CLI `embedding-migrate`도 같은 `rag/embedding/schema.sql`을 읽는다.

## 실행·검증·평가·실험의 내부 책임

실행 중 검증은 원본 해시·ID·경로·인용의 일관성을 검사한다. 평가는 별도 참조와
저장 결과를 대조하거나 같은 기준의 실행 점수를 비교한다. 검사 통과는 의미 정답 승인이 아니다.
`parser/structure/validation.py`, `rag/evidence/`의 출처·인용 검사와 에이전트 응답 검사는
실행에 필요한 기능이므로 해당 계층에 유지한다. 모델 Judge도 검토 후보를 만드는 실행 역할이며,
사람 정답 대비 성능 평가와 구분한다.

| 위치 | 내부 책임 |
|---|---|
| `evaluation/parser/golden.py` | 고정 정답의 표 탐지·셀 기하·병합 비교 |
| `evaluation/parser/tree_accuracy.py` | 원본 근거와 행·열 경로 정답 비교 |
| `evaluation/parser/reference_checks.py` | 중첩 관계·읽기 순서·인용 등 외부 참조 검사 |
| `evaluation/parser/snapshots.py` | 수정 전후 셀·경로·원문과 기존 중첩 연결 보존 비교 |
| `evaluation/parser/run_comparison.py` | 이미 평가된 점수의 분모·참조·버전 일치 검사와 회귀 기록 |
| `evaluation/parser/physical_recovery.py` | 물리 복원 실험 결과를 외부 셀·경로 참조로 채점 |
| `evaluation/ledger.py`, `sample_unseen.py` | 평가 기록·검토 이력과 미사용 자료 선정 |
| `validation/source_integrity.py` | 검토 큐와 팀이 사용하는 원본 해시·span·경로 참조 검사 |
| `validation/arithmetic.py` | 명시적 공란·대시 정책에 따른 Decimal 해석·합계 검산 |
| `experiments/parser_repair/engine.py` | 코드 사본·수정 제안·격리 실행을 연결하고 평가기 호출 |
| `experiments/parser_repair/automatic.py`, `registered_plan.py` | 제한된 수정 제안과 반영 조건·동결 제약 처리 |
| `experiments/parser_repair/parser_refresh.py`, `queue.py` | 저장본과 현재 코드 재추출 비교·실험 상태 관리 |
| `review/presentation/metrics.py` | 공통 표시 결과에서 표 수·경로 연결 수 집계. 정답 정확도 점수가 아님 |
| `review/presentation/document.py` | 동결된 본문·중첩 표 HTML 표시 함수를 그대로 보존하여 웹·실험에서 공유 |
| `review/presentation/structure.py` | 구조 후보의 HTML·PDF 위 표시 이미지 생성. 분석기는 실행하지 않음 |
| `experiments/physical_recovery/` | 짧은 선·래스터·텍스트 정렬을 이용한 복원 후보 알고리즘 |
| `services/structure_review.py` | 물리 IR에서 구조 분석 실행·후보 JSON·스키마·요약 저장 및 검토 표시 호출 |
| `scripts/evaluate_physical_recovery.py` | CLI 입력·전략별 집계·저장. 개별 사례 채점은 평가 패키지 호출 |

의존성은 `실험 실행 → 평가`, `검토 실행 → 무결성 검사` 방향이다.
`evaluation/parser/`는 실험 실행기·모델·웹·파서를 import하지 않는다.
`run_comparison.py`의 비교 API를 옮겼다고 캠페인에 정답 승인·전체 평가가 새로 연결된 것은 아니다.
RAG·에이전트의 별도 성능 평가기가 추가될 때 각각 `evaluation/rag/`, `evaluation/agents/`에 둔다.
아직 없는 평가 구현을 빈 폴더로 만들어 구현된 것처럼 표시하지 않는다.

기존 `experiments/parser/source_checks.py`는 `reference_checks.py`와 `snapshots.py`로,
`agents/workflows/campaign/evaluation.py`는 `run_comparison.py`로 이동했다.
`evaluation/semantic.py`의 실행 책임은 `services/structure_review.py`에, 표시 책임은
`review/presentation/structure.py`에 둔다. 일반 CLI도 사용하는 서비스이며 평가기나 실험 전용 코드가 아니다.
`write_evaluation()`은 `write_structure_review()`로 명확히 이름을 바꿨다.
기존 CLI 명령·인자와 산출물 JSON 키는 유지하며 구경로 재노출용 복사본은 두지 않는다.
기존 `source_checks` 결과 키와 큐 모드·버전은 저장된 실행을 읽기 위해 유지한다.
정답·fixture·기존 실험 결과·동결 manifest/ZIP은 삭제하거나 재생성하지 않는다.

웹 진입점은 `review/presentation/document.py`, 수정 실험은
`review/presentation/metrics.py`를 사용한다. 집계는 동결된 표시 함수를 그대로 재사용하므로 HTML 생성도 수행한다.
표시와 집계를 내부적으로 더 분리하는 변경은 현재 동결 범위에 포함되어 보류한다. 이전 `web/document_view.py`,
`experiments/parser_repair/diagnostics.py`, `experiments/structure/`에는 호환용 복사본을 남기지 않는다.
`parser/physical/runner.py`의 기존 `evaluate_pdf()` 이름은 동결 보호 대상으로 유지한다.
