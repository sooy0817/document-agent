# Phase 2 이후 구현 계획 및 모듈 경계

> 최신 상태: Phase 2-A 물리 격자 컴포넌트는 구현했다. 아래 계약-only 상태는 Phase 0/1 당시
> 계획 기록이며 현재 상태는 PHASE2A_RESULT.md와 ADR-005를 우선한다. 의미 구조/템플릿/LLM/DB는 계속 후속이다.

## 현재와 다음 단계의 경계

현재 실행: `PDF bytes -> PDFExtractor -> DocumentIR -> run-local files`.
현재 미구현: segmentation, table analysis, registry, matching, hybrid interpretation,
canonical transformation, financial validation, PostgreSQL persistence.

Phase 2를 착수하기 전에 PyMuPDF/FastAPI 통합 테스트의 skip을 제거하고 합성 PDF의
셀 bbox·빈칸·병합 후보·페이지 렌더를 실제 확인해야 한다. Phase 1의 실행 검증이 완료되지
않았으므로 이번 작업에서 Phase 2 알고리즘을 구현하지 않았다.

| 모듈 | 입력 → 출력 | 책임 / 의존성 | 상태 |
|---|---|---|---|
| ingestion/artifacts | bytes → ArtifactRef | SHA-256, run-local append-only IO | 구현 |
| extraction | ExtractionRequest → DocumentIR | native text/geometry/pixels/table candidates; domain·PyMuPDF·ArtifactStore | 코드 구현, 통합 검증 대기 |
| domain/models | JSON ↔ typed evidence | Page/Block/Span/Cell/SourceRef, ID·출처 일관성; Pydantic만 | 구현·단위 검증 |
| orchestrator | 요청+PDFExtractor → snapshot | 독립 실행, 입력 제한, 실패 기록; interface 의존 | 구현·fake adapter 검증 |
| segmentation | DocumentIR → SegmentedDocument | 섹션/콘텐츠 연결, 읽기 순서, 인접표 후보; domain | 계약만 |
| table_understanding | PhysicalTableIR → StructuredTableIR | 그리드·패널·계층·매핑·주석·구조 검증; domain만 | 계약만 |
| templates | structured evidence+registry → MatchResult | 승인 버전, 필수/부정 조건, 미일치 근거 | 계약만 |
| interpreters | route+evidence → InterpretationResult | deterministic/guided/generic, 제한된 patch | 계약만 |
| validation/normalization | canonical+evidence → ValidationReport/fact candidates | Decimal, 단위·기간·수치·출처·산식 | 계약만 |
| persistence | canonical+approved facts → DB | JSONB snapshot, transaction, idempotency; SQLAlchemy | Base/Alembic scaffold만 |
| api | PDF body → DocumentIR | 로컬 HTTP facade; workflow 의존 | 코드 구현, 통합 검증 대기 |

domain/pipeline_contracts.py는 후속 통합 계약 모음이다. table_understanding은 이를 역으로
import하지 않는다. 현재 Protocol/스키마가 존재한다고 알고리즘 또는 승인 gate가 구현된 것은 아니다.

## Phase 2 상세 순서

1. **TS-0 계약 및 골든**: DocumentIR의 raw candidate를 PhysicalTableIR로 어댑트. 셀 ID 불변,
   불명확 span은 null. Logical table은 source 후보 ID 여러 개를 참조. 좌표/패널별 기대값을
   사람이 확인한 synthetic golden과 실제 공시 golden으로 분리한다.
2. **Segmentation 최소 구현**: raw extraction_order를 의미 읽기 순서로 오인하지 않는다.
   섹션 제목·본문·주석 후보, 인접 페이지의 유사 열/제목 단서와 단위/새 제목의 부정 단서를
   남긴다. 연속표 확정은 assembler가 소유한다.
3. **TS-1 Physical Structure Analyzer**: 선·셀 bbox의 경계 집합, row/column interval,
   rowspan/colspan 복원. null detector slot의 merged/unknown 구분. 독립 좌우 패널/표 분리.
   격자 결손을 임의 빈 셀로 만들지 않는다. 병렬표의 같은 y를 같은 계정 행으로 합치지 않는다.
4. **TS-2 Structural Feature Classifier**: 다단 열/계층 행/병합/병렬/연속/반복 헤더/합계/주석
   등의 다중 라벨과 각각 evidence, heuristic_id, disposition을 남긴다.
5. **TS-2 Semantic Structure Resolver**: Column Tree/Row Tree, 반복 이름의 full parent path,
   panel별 cell-to-header/row mapping, subtotal/total의 구조상 유형, 주석 scope를 생성한다.
   행 계층을 산술 합계 관계로 간주하지 않는다. 해결되지 않은 매핑은 unresolved로 남긴다.
6. **TS-2 Structure Validator**: 부모 존재/순환, 격자 중복, source membership, 셀 coverage,
   panel crossing, repeated-header 중복, continuation 근거를 점검한다. 비어 있지 않은 셀은
   mapped/header/row-label/note/unresolved 중 disposition이 있어야 한다. unknown도 보존한다.
7. **TS-3 선택적 patch 계약 검증**: 원본을 바꾸지 않는 revision patch만 허용. 허구 ID,
   새 숫자, bbox 변경, parent cycle을 거절한다. 공급자 연결과 실제 호출은 Phase 4에서 수행.
8. **Gate**: 합성 골든 header-path/cell mapping/bbox/page 일치, negative fixture에서 잘못된
   validated 상태 0건, 전체 Phase 1 회귀 통과. 실문서 결과는 별도 평가로 표기한다.

### Phase 2 파일 계획 (아직 파일/알고리즘 없음)

`segmentation/{blocks,reading_order,continuation,document_tree}.py`

`table_understanding/{assembler,physical_grid,panel_detector,feature_classifier,
header_tree,row_tree,cell_linker,context_linker,validator,orchestrator}.py`

| Fixture | 반드시 확인할 정답 | 목표 단계 |
|---|---|---|
| 단순 격자 | 원문/빈 셀/0/음수, bbox, row/col index | Phase 1 통합 + Phase 2 grid |
| 예적금 다단 헤더 | 2024/2025 → 잔액/구성비/평잔/구성비 full paths | Phase 2 |
| 계층형 손익계산서 | 부모 계정, 합계/소계 구분, 반복 기타의 부모 | Phase 2 |
| 자산·부채 병렬표 | 독립 panel, y가 같아도 cross mapping 없음 | Phase 2 |
| 연속표/반복 헤더 | 물리 페이지 보존, 논리 연결, 중복 값 없음 | Phase 2 |
| 다른 표가 이어지는 페이지 | 부정 단서로 잘못된 합치기 거절 | Phase 2 |
| 표+문단+주석 | 원문 보존, 적용 범위 또는 unresolved | Phase 2 |
| 일부 일치/추가 행·열 | 미등록 셀 보존, 국소 guided 범위 | Phase 3~4 |
| 신규 양식/제목만 같은 표 | UNKNOWN, 잘못된 MATCHED 차단 | Phase 3 |
| 빈칸/0/-/△/괄호 | raw exact match, 정책 없는 부호 확정 금지 | Phase 1/5 |
| 이미지 PDF | phantom cells 없음, OCR_REQUIRED, source image | Phase 1/4 |
| 악성 PDF 내 지시문 | 데이터로 보존, 도구/네트워크 명령 실행 없음 | Phase 4 |

## Phase 3 — Template Registry

JSON 저장소는 document/section/content 3계층으로 분리. template ID+version+content hash,
draft/approved/deprecated, immutable published definition. 파일 registry를 Protocol로 제공하고
Phase 5 PostgreSQL registry adapter로 교체 가능하게 한다. 초안 seed는 승인하지 않는다.

제목 + 헤더 경로 + 행 구성 + 단위 + 비교기간 + 패널/레이아웃 + 부정 조건을 evidence와 함께
평가한다. 단일 score를 확률로 해석하지 않는다. 구조 partial이면 MATCHED 자동 승인 불가.
동점/모호함은 AMBIGUOUS→review. 오류의 우회 경로로 UNKNOWN→자동 Fact 발행하지 않는다.

## Phase 4 — Hybrid Extraction / LLM

- MATCHED: 검증된 영역은 deterministic. PARTIAL: unmatched_source_ids만 guided.
  UNKNOWN: 범용 구조 해석. AMBIGUOUS/UNSUPPORTED: review.
- OpenAI 구현은 LLMVisionAdapter 뒤에 둔다. crop, span/cell/bbox, 구조 후보, 템플릿,
  인접 페이지 문맥, 허용 ID 목록, strict schema만 전송한다. PDF 내용은 untrusted data다.
- 명확한 구조는 LLM 0회. 기본 구조 재해석 총 1회; 공급자 timeout·429·5xx 전송 재시도는
  별도 설정으로 제한하고 총 호출/시간/토큰 budget에 포함한다.
- timeout/provider_error/invalid_json/불확실성은 원본 IR+issue 유지 후 review/partial.
  Pydantic 통과 후에도 source ID membership, topology, 원문 수치 불변을 검사한다.
- 실패/허구 숫자/새 ID/무한 retry/mocked timeout을 테스트. 실제 API 비용 발생 테스트는 별도 승인·예산.

## Phase 5 — Validation & PostgreSQL

CanonicalDocument 초안의 필수 키는 document_id/institution/reporting_period/sections/contents/
table_structure/column_hierarchy/row_hierarchy/cell_mappings/notes/source_references/
template_match/validation_status/issues다. 행/열의 루트 요약 배열은 table_id로 구별할 수 있게
스키마를 확정하며, 중복 저장 시 내용 불일치 validator가 필요하다.

JSONB 후보 snapshot은 보존하되 미검증 수치를 financial_facts에 넣지 않는다. candidate
observation은 별도 staging/JSONB에 둔다. FinancialFactCandidate의 status 필드는 권한이 아니며
repository가 원본·승인 report·run을 검증해야 한다.

기관, 원본 문서, 불변 run, structure snapshot, template match, review, validation, approved facts,
source links를 migration으로 생성한다. 승인된 fact만 트랜잭션으로 발행한다. 멱등성 키는
run/source observation에 묶고 재시도 중복, partial rollback, 동시 발행, supersession을 테스트한다.
값은 Decimal/NUMERIC, 원본 단위/정규화 단위, 시점/기간/비교 기준, 회계 범위를 별도 보존한다.

## Phase 6 — Integration & Evaluation

강구 공시 p18·재무상태표·손익계산서·감사보고서의 수동 정답을 수집하고, 다른 기관 2곳 이상 및
다른 기간 2개 이상의 holdout을 분리한다. table cell recall, header/row edge F1, cell mapping
정확도, continuation precision/recall, note scope, false MATCHED, LLM 호출률/비용을 분리 보고한다.
현재 이런 정확도/비용 결과는 없다. 실제 PDF의 골든 검증 없이 전국 공시 호환을 주장하지 않는다.
