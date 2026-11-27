# 실행 코드의 도메인 가정 정리 — 2026-11-09

현재 작업 브랜치: `codex/refactor-20261109-generic-cleanup`.
등록된 worktree는 개발 루트 하나다. 기존 metric 정리 변경을 보존한 상태에서
실행 코드의 고정 입력을 추가로 제거했다. 아래 목록은 범용 동작을 확인하기 위한
코드 점검이며, 모든 문서에 대한 정확도 인증이 아니다.

## 변경한 실행 경로

| 파일 / 영역 | 이전 가정 | 변경한 동작 |
|---|---|---|
| `document_rag/graph.py`, `constraints.py`, `understanding.py` | 농협·축협 접미사만 원문 기관으로 인식 | 원문에 근거한 출처명 또는 명시적 `required_sources`; 복수 출처 각각 확인, 추측 출처 제외 |
| `document_rag/service.py`, `graph.py` | 기본/Agent/문장형 검증 실패 처리 차이 | 모든 모드에서 검증 실패 시 보류; Agent 재검색은 최대 1회 |
| `document_rag/service.py` | 기간은 열에만 있고 질문은 금액 위주 | 행·열 기간 모두 확인; 비재무 수량·온도 질문도 보수적 검사 |
| `document_rag/index.py` | 셀 본문을 찾았어도 경로만으로 정렬 | 셀 원문+행·열 경로로 정렬; 텍스트 셀 회귀 추가 |
| `table_understanding/structure/analyzer.py`, `structure/models.py` | 명시적인 HTML 헤더에도 기하 추정 불확실성 적용 | 명시적 레이아웃 유지, 분석 버전 0.3.1; 힌트 없는 PDF 규칙 유지 |
| `document_rag/answer.py`, `model_policy.py`, `understanding.py`, `telemetry.py` | GPT-4.1만 허용하고 비용/트레이스 모델도 고정 | 모델 선택과 명시적 단가, 호출 전 예산 검사, 실제 모델 기록; 재무 전용 안내 제거 |
| `document_rag/narrative.py` | 대문자 2~8자 대상명, bullet 조건만 처리 | 실제 행 라벨·원문 별칭에서 대상 후보; bullet 없는 조건문도 포함 |
| `document_rag/batch.py` | `연말결산/2025`, 요약·정기 우선, 남평 자동 제외 | 입력 root/glob/제외 경로·해시; 원문 중복 제거와 부모 폴더별 표본 균형 유지 |
| `document_rag/__main__.py`, `scripts/run_langgraph_rag.py` | 검색/근거 한도 및 모델 옵션 일부 숨김 | 출처 제약·검색 수·문서 수·셀 수·모델·단가·비용 상한 옵션 |
| `eval/sample_unseen.py` | 남원·남평·강구 이름 자동 제외 | freeze의 명시적 제외 이름/ID와 prior_exposure; 누출 그룹 차단 유지 |
| `cli.py` | `physical-grid` 기본 관심 페이지 8,14,15,18 | 기본 전체 페이지; focus-only는 페이지 명시 필요 |
| `runtime/catalogs.py`, `web/app.py` | 특정 은행 문서·두 fixture를 기본 샘플로 자동 등록 | `DOCUMENT_SAMPLE_CATALOG`로 선택한 PDF만 표시; 기본 샘플 없음 |
| `teams/campaign/__main__.py`, `pilot.py`, `web/app.py`, `campaign.js` | N03/N08 자동 선택, 13항목·39회 가정 | manifest 필수/명시적 환경 설정, goal·실제 항목 수·limits; 기존 실행 기록 유지 |
| `teams/campaign/public_sources.py`, `public_download.py` | 한국은행/국토연구원과 권리 문구가 코드에 고정 | `DOCUMENT_PUBLIC_SOURCE_CATALOG` 정책; 기본 외부 수집 없음; 기존 출처는 선택적 예시 JSON |
| `human_review/app.py`, `precheck.py`, `simple.html` | architecture-v1/남원 검산을 기본 UI·원본 조회에 연결 | 범용 구조 검토 기본 화면; precheck/cases/source_catalog 명시; 고정 표본 수 안내 제거 |
| `semantic_description/routes.py`, `store.py`, `guidance.py` | 과거 review-pack·supplement를 자동 적용, mock 파일명 판정 | 명시적 검토 자료, 없으면 빈 목록; 저장된 origin으로 테스트 데이터 판별 |
| `web/static/index.html`, `monitor_model.js` | 새 문서 10건, 존재하지 않는 demo 경로, 모델명 고정 | 실제 목록, 현재 팀 경로, 기록된 모델명 표시 |

기존 작업에서 삭제된 `metric_mapping/` 10개 파일과 `staging/{batch,five_report,
metric_candidate,report}.py`, 해당 테스트 2개는 복원하지 않았다. 이 삭제는 이번
턴 이전부터 존재했던 변경이다. 원본 DB·평가 기록·baseline은 삭제하지 않았다.

## 입력과 재사용

```powershell
# 아래 카탈로그는 선택 사항이다. 지정하지 않으면 해당 샘플/외부 출처는 없다.
$env:DOCUMENT_SAMPLE_CATALOG = 'config/examples/samples.json'
$env:DOCUMENT_PUBLIC_SOURCE_CATALOG = 'config/examples/public-sources.json'
# 원하는 development manifest를 골랐을 때만 지정한다.
$env:DOCUMENT_CAMPAIGN_MANIFEST = 'data/campaign-catalog/your-selection.json'
```

카탈로그는 UTF-8 JSON이다. 샘플은 `{id,name,path}` 목록, 공개 출처는
`{id,organization,domain,url,host,path}` 목록이다. `rights_markers`가 없는 출처는
링크 후보만 수집한다. 다운로드 가능한 출처는 비어 있지 않은 문구 배열을 명시한다.
문구 관찰은 이용조건의 법적 판정이 아니다. 자동 경로는 실행 요청 전에는 호출되지 않는다.

RAG의 `--required-source`는 여러 번 지정할 수 있다. 문서 수와 셀 한도는
`--max-documents`, `--max-cells-per-document`, 검색 수는 `--limit`로 지정한다.
다른 모델은 `--model`, `--input-usd-per-million`, `--output-usd-per-million`을 함께
지정한다. `--max-cost-usd`는 호출당 한도이며 모델을 호출하려면 기존처럼
`--allow-external`이 필요하다. 기본 GPT-4.1 단가는 과거 프로젝트 추정치이며 현재
가격을 조회한 것이 아니다. 모델의 Responses/JSON schema 지원은 별도 조건이다.

배치 inventory의 `--year`는 제거했다. 기간 폴더 자체를 `--root`로 지정하거나
`--pattern`을 사용한다. 예전 holdout 재현은 `--exclude-path`/`--exclude-sha256`,
표본 추출은 freeze의 `excluded_institution_ids`/`excluded_institution_names` 및
`prior_exposure`를 명시해야 한다. 자동 제외 이름을 제거했다고 기존 holdout을
개발 데이터로 사용해도 된다는 뜻은 아니다.

캠페인 manifest의 선택적 `limits`는 `max_items`(기본 20, 최대 20),
`max_daily_calls`(기본 20, 최대 200)다. 기존 공유 검토 설정·호출 원장은 덮어쓰지 않는다.
기존 캠페인은 원래 manifest/spec/해시로 재개되며, 코드 변경 후 재개는 기존 해시
검사가 막을 수 있다. 새 실행을 만들거나 기존 검토 절차를 따라야 한다.

## 보존하는 가정과 제한

- 물리 파서 22개 보호 파일, KB baseline 6개와 archive: 동결 계약이므로 유지.
  `teams/semantic_campaign/service.py`와 `check_parser_freeze.py`의 날짜 경로는
  보호해야 할 baseline의 정체성이며 임의 기본 문서 선택이 아니다.
- `domain/models.py`, `domain/pipeline_contracts.py`, `semantic_resolver/{models,
  artifacts,engine}.py`, `cli.py`의 accounting_scope: 과거 저장 계약 호환용.
  unknown 또는 호출자 입력이며 금융 분류·Fact 발행을 자동 수행하지 않는다.
- `runtime/{model_client,review_queue}.py`, `teams/table_review/service.py`,
  `table_understanding/review/axis_judge.py`, `relations/continuation_provider.py`, `context/period_context.py`,
  `parser_experiments/{engine,automatic,queue}.py`: 스키마·가격·예산 및 실험 재현과
  결합된 모델 프로토콜. 임의 모델 호환성을 주장하지 않으며 RAG 모델 설정과 구분한다.
- 한국어/영어 토큰화, 조건·최신성·확실성의 보수적 언어 규칙은 남아 있다.
  범용 NER/다국어 의미 검증기가 아니다. 출처명 검사도 파일명의 문자 근거 수준이다.
- 헤더·stub 추정, 부분 열 구조, 계속표, OCR, 미매핑 셀의 검색 보장은 남은 한계다.
  원문 전체 임베딩·벡터 검색·GraphRAG를 이번 정리에서 구현한 것은 아니다.
- 수량·거리·예산 같은 상수 모두가 도메인 하드코딩은 아니다. 타입 계약, 안전 상한,
  기하 허용 오차와 재현 seed는 근거 없이 없애지 않는다.

## 전체 파일 점검과 과거 실험

`scripts/audit_domain_assumptions.py`가 src/scripts/tests/config의 파일별 경로,
SHA256, 발견 종류와 행 번호를 출력한다. 검색 패턴은 기관명, 날짜가 붙은 과거
자료 경로, 개인 절대 경로, 고정 모델명, 과거 도메인 필드를 분리한다. 패턴에 안
걸리는 논리 가정은 수동 점검이 필요하다. `.agent/`, 원본 자료, 과거 보고서,
별도 `github-portfolio/`와 `public-release/`는 현재 실행 소스와 구분해 보존한다.

특정 실험을 재현하는 scripts는 과거 자료의 의미를 바꾸지 않도록 보존한다.
현재 일반 실행 진입점은 [scripts 안내](../scripts/README.md)에 명시했다.
파일별 전체 결과는 `reports/generic-runtime-cleanup-20261109/domain-assumptions.json`,
검증 결과는 같은 폴더의 `validation.json`에 기록한다.

과거 절대 경로가 있는 `prepare_semantic_corpus.py`, `evaluate_namwon_holdout.py`,
`run_cross_institution_smoke.py`, `extend_smoke_native.py`는 특정 코퍼스 재현용이다.
일반 입력에는 이를 사용하지 않고 `document_rag.batch inventory` 및 명시적
campaign manifest를 사용한다. 이 스크립트가 범용화됐다고 표시하지 않는다.

계약 변경 근거: [ADR-075](ADR/075-explicit-domain-independent-runtime.md).

## 검증 시점과 병행 변경

전체 회귀 검사(폴더 분리 전): 751 passed, 4 failed, 1 skipped, 13 deselected.
13개는 Windows asyncio socketpair에서 정지하는 HTTP/비동기 테스트다.
실패는 과거 semantic-c1 계약 해시, table identity 오류 코드 순서, PyMuPDF 이미지 입력 2건이며
원래 HEAD 코드에서도 동일한 문제를 확인했다. 검사 기준을 바꾸거나 실패를 숨기지 않았다.
이후 다른 작업의 table_understanding 폴더 분리가 감지되어 해당 변경을 보존했다.
이동된 파일에 이번 의미 수정이 유지되는지 및 관련 회귀 80개가 통과했다. 현재 동결 검사도 29개 통과했으며,
이동된 물리 코드의 실행 AST 동등성과 KB 산출물의 원본 해시를 검사한 결과다.
감사 목록은 코드·스크립트·테스트·설정 444개 파일, 패턴 발견 93개 파일을 기록했다.
전체 통과 및 변경 소유권 분리 조건을 충족하지 않아 자동 커밋은 보류한다.
