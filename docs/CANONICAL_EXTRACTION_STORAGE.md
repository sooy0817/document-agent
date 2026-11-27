# Canonical Document JSON / PostgreSQL 저장 설계 초안

현재 구현은 `canonical-extraction-envelope/0.1-draft` Pydantic/JSON Schema와 로컬 예시뿐이다.
DB 연결·DDL 실행·마이그레이션·적재는 하지 않는다. 기존 CanonicalDocument 1.0 초안과 기존
DocumentIR/PhysicalTableIR/StructuredTableIR/SemanticOverlay를 변경하지 않는다.

## 원본 보존 구조

Envelope는 source_document_id, source_version_sha256, source_uri, parsing_run_id,
parsing_run_status, configuration, artifacts, failures를 가진다. artifact는 원본 URI와
raw_sha256, JSON payload, schema_version, upstream_artifact_ids, 원본 page/node 참조,
validation_status를 보존한다. invalid/review_required도 저장 대상이다.
Schema 유효성, 기하 검증, 의미 검토, 재무 검증 상태를 서로 승격하지 않는다.

원본 PDF와 원본 JSON 바이트는 불변 파일/객체 저장소에 둔다. JSONB는 검색용 projection이며
원본 해시를 JSONB 재직렬화로 계산하지 않는다. PostgreSQL JSONB는 공백·key 순서·중복 key를
보존하지 않으므로 원본 바이트 별도 보존이 필요하다.
[PostgreSQL JSON 문서](https://www.postgresql.org/docs/current/datatype-json.html).

| 제안 테이블 | 키와 주요 필드 | 역할 |
|---|---|---|
| source_document | document_uuid, 기관/공시 식별 메타데이터 | 논리적 문서. 파일명만으로 확정 동일성을 판단하지 않음 |
| document_version | version_uuid, document_uuid FK, sha256, byte_size, mime, immutable_uri | 동일 파일 내용 중복 감지. 개정판은 새 version |
| parsing_run | run_uuid, version_uuid FK, pipeline/config/code/model hashes, status, started/ended, failure JSONB | 실패·부분·성공 실행 모두 기록; 시도별 이력 |
| page | run_uuid + original_page_number, dimensions/rotation, source_refs JSONB | 파싱 실행별 페이지; 빈/스캔 페이지도 보존 |
| table_region | run_uuid + table_id, page FK, bbox JSONB, analysis_state | 탐지 후보와 거절 이력 구분; 의미 확정과 무관 |
| extraction_artifact | artifact_uuid, run_uuid FK, kind, schema_version, raw_sha256, immutable_uri, payload JSONB | DocumentIR, PhysicalTableIR, StructuredTableIR, SemanticOverlay 원형 스냅샷 |
| artifact_dependency | artifact_uuid + upstream_uuid | 재처리 DAG; 순환 금지, 누락 참조 금지 |
| patch_proposal | proposal_uuid, artifact_uuid FK, request/registry hash, original_response JSONB, operation JSONB | rejected/ABSTAIN 포함 원래 제안과 바인딩 결과 보존 |
| semantic_revision | revision_uuid, baseline_uuid FK, proposal_uuid FK, before/after JSONB, status | 새 해석 버전. 원본 IR update 금지 |
| aggregation_scope | revision_uuid + scope_id, payload JSONB | parent_child와 별개 subtotal/total membership |
| validation_event | event_uuid, artifact/proposal FK, validator/version, stage, status, issues JSONB, reviewer | 자동 검증과 독립 사람 승인 구분; append-only |
| evidence_link | target UUID, source artifact FK, page, original node/cell/span ID, bbox, evidence_state | 원본으로 역추적. observed/inferred/unresolved 보존 |
| financial_fact (후속) | fact_uuid, approved_revision FK, metric/scope/period/unit/value, evidence FK | 별도 승인된 fact만; 현재 구현/발행 경로 없음 |

관계 PK/FK는 UUID/실행 범위로 관리하고 원본의 긴 node_id는 문자열로 보존한다.
원본 cell_id가 여러 재처리 실행에 반복되어도 run_uuid와 함께 식별한다.
금액은 원문 문자열을 항상 보존하고, 후속 변환은 numeric/decimal과 단위·부호·기간을 별도 저장한다.
null/빈칸/대시/0을 합치지 않는다. 상태를 근거로 금액을 보정하지 않는다.

## 저장·재처리·발행 경계

1. source/version 등록 후 parsing_run을 먼저 생성하여 파싱 전체 실패도 누락되지 않게 한다.
2. artifact를 임시 위치에 쓰고 해시 검증 후 불변 URI로 확정한다. DB transaction은 메타데이터와
   해당 artifact의 증거/의존성 참조를 함께 커밋한다. 객체 저장소와 DB 불일치는 reconciliation 대상이다.
3. 멱등성 키는 source version + pipeline/config/model/code hash + stage + request digest.
   같은 키의 재시도도 attempt 이력은 보존하며 완료 artifact를 덮어쓰지 않는다.
4. partial/invalid 문서도 원본·실패 사유·검토 대기 상태를 저장한다. 재처리는 새 run과 새 revision.
5. Raw 저장 권한에는 fact 테이블 쓰기 권한을 주지 않는다. Fact 발행은 별도 서비스/transaction으로
   원본 근거, semantic 승인, scope/기간/단위, 필수 재무 검증, reviewer를 확인해야 한다.
6. 현재 envelope의 `fact_publication_allowed=false`, `financial_facts=[]`는 계약상 강제한다.
   이는 DB 권한/트리거를 구현했다는 뜻이 아니다. 향후 DB에서도 독립적으로 enforcement한다.

초기 인덱스: version.sha256, run(version,status), artifact(run,kind), table(run,page),
evidence(source_artifact,node_id), proposal(request_digest). JSONB GIN은 실제 쿼리에 필요한
경로만 검증 후 추가한다. 대용량 PDF/image bytes를 JSONB에 base64로 중복 저장하지 않는다.

## 적재 MVP 범위 / 아직 필요한 검증

- 원본 파일 등록, content hash, run/attempt, JSONB artifact와 provenance의 단일 문서 적재.
- 실패 문서, partial table, rejected patch, 명시적 기권 및 미해결 coverage 보존.
- 재실행 멱등성, 강제 중단 후 복구, 부분 transaction rollback, FK 교차-run 검증,
  원본 파일 재해시, export/reload 동일성, 승인 없는 Fact write 차단 테스트.
- 실제 마이그레이션은 별도 작업. 지금 저장 준비도는 **계약/설계와 로컬 예시 단계**이며
  PostgreSQL 운영 준비 완료나 검증된 Financial Fact 적재 준비 완료로 간주하지 않는다.

현재 예시는 DocumentIR와 SemanticOverlay 두 스냅샷을 담은 축약 예시다. 전체 적재 MVP에서는
Overlay.input_hashes에 기록된 모든 Physical/Structured artifact를 dependency와 함께 등록해야 한다.
