# ADR-004 — 신규 프로젝트와 Phase 1 증거 계약

- 일자: 2026-10-16
- 상태: Phase 0/1 구현 결정. Phase 2 이후 인터페이스는 제안 상태.
- 기준: DESIGN.md, TABLE_STRUCTURE_UNDERSTANDING.md, ADR-003, 사용자 신규 구축 요청

## 조사 결과 및 충돌 해결

| 항목 | 기존 설계 / 실제 상태 | 이번 결정 |
|---|---|---|
| 기존 파서 보존 | 설계는 nh_pipeline/nh_parsing와 기존 커밋을 전제하나 ZIP에는 설계 파일 12개만 존재 | 신규 src/doc_understanding 생성. 가상의 기존 파서·회귀 성공을 주장하지 않음 |
| 단계명 | A/B/B2/C, P0/P1/P1.5, 사용자 Phase 0~6 혼재 | 이번 보고서는 사용자 Phase 0~6 사용. B2/P1.5는 Phase 2에 대응 |
| 원본 지침 | README는 루트 지침 복사를 제안 | .agent 원문 유지, 루트 AGENTS.md가 참조 |
| 문서 ID | IR의 doc_sha256, sha256, DDL UUID 혼재 | IR 문서 ID는 sha256:<원본 PDF SHA-256>. 실행 ID는 UUID. Phase 5 내부 DB PK를 UUID로 쓰더라도 외부 content ID와 분리 |
| 물리 셀 / 의미 | 기존 Table IR에 merged_headers, side_panels 포함 | Phase 1은 CellIR/TableCandidateIR. 의미 노드·패널·병합 확정은 Phase 2 PhysicalTableIR/StructuredTableIR |
| 병합 셀 | 알 수 없는 rowspan/colspan을 1로 강제 금지 | Phase 1에서는 모두 null. detector_grid의 null 슬롯은 빈 셀이 아님; 원본 bbox·슬롯 보존 후 Phase 2가 판정 |
| 좌표 | PDF points/top-left만 정의 | 회전 전 CropBox 기준 좌표. original_rotation/cropbox 저장. 페이지 이미지도 회전 전으로 렌더링하며 point→pixel 6계수 저장. 디스크 PDF는 불변 |
| 페이지 hash | 정의 불명확 | evidence_sha256는 직렬화한 native block/table 증거 해시. 렌더 PNG 해시는 별도. 원본 PDF 페이지 바이트 해시라고 부르지 않음 |
| 매칭명 | verified_match/partial_match/unmatched와 MATCHED/PARTIAL/UNKNOWN | 후속 계약에 MATCHED/PARTIAL/UNKNOWN/AMBIGUOUS/UNSUPPORTED. MATCHED는 발행 승인 상태가 아님 |
| 재시도 | 전체 설계 예시 2회, 표 레이어 최대 1회 | 구조 재해석 총 1회(기본 비활성), 전송 재시도는 별도 bounded 정책으로 Phase 4 구현. 재시도가 새 의미 해석 횟수를 초기화하지 않음 |
| 원본 수치 | 표 예시에 Decimal 포함 | Phase 1은 text_raw만. 0/빈칸/△/괄호/- 해석은 하지 않음. Decimal 및 관측기간은 Phase 5 |
| 최종 출력 | Document JSON과 원시 IR 혼동 가능 | document-ir/1.0은 증거 중간표현이며 validation_status=not_validated 고정. CanonicalDocument는 별도 draft 계약 |
| API | 비동기 ingest/extract/run/review 엔드포인트 설계 | Phase 1에는 로컬용 /health와 POST /v1/ir/extract만. 미구현 엔드포인트를 성공 응답으로 흉내 내지 않음 |
| 문서 메타데이터 | 기관/보고기간 추론 규칙 미정 | caller_supplied 또는 unknown. 파일명으로 자동 확정하지 않음 |
| 스캔 | OCR/vision 선택 필요 | PNG+이미지 블록 보존, native text 없는 시각 페이지는 OCR_REQUIRED. OCR 자체는 미구현 |
| 테이블 독립성 | ADR-003은 src/doc_understanding/table_understanding 경로 지정 | 해당 독립 Python 패키지 유지. domain만 의존, template/DB/provider SDK import 금지. 별도 배포 wheel 분리는 추후 결정 |
| 표 초안 | scope=regular_disclosure, schema_version/expected_structure 등 누락 | .agent 초안 그대로 보존. Phase 3에서 document_type와 accounting_scope 분리하고 스키마 보완. 승인된 템플릿으로 로드하지 않음 |

## 증거 보존 규칙

1. 원본 PDF bytes의 SHA-256이 문서 식별자다. 파일명 변경과 무관하다.
2. 각 실행은 새 UUID 디렉터리에 source.pdf, 페이지 PNG, 이미지, document-ir.json을 기록한다.
3. 동일 bytes + 동일 엔진/정책의 증거 ID는 동일하다. 엔진 버전이 달라지면 run을 구분하며 동일 ID에 동일 해석을 보장하지 않는다.
4. Span text는 엔진이 반환한 문자열 그대로다. Block text는 줄별 span을 결합한 편의 표현이며 PDF 원시 바이트와 동일한 문자열이라고 주장하지 않는다.
5. 텍스트 span과 table cell은 같은 영역을 다른 관점에서 표현한다. 중복 제거로 어느 쪽도 버리지 않는다.
6. 기하 기반 span_refs는 후보 연결이다. 의미적 header/row mapping으로 취급하지 않는다.
7. Pydantic frozen + tuple은 정상 사용에서 변경을 막는다. 보안 권한 경계는 아니며 workflow가 직렬화 후 재검증한다.
8. 모델 검증은 참조 일관성 검증이다. 실제 PDF 대조/헤더 경로/회계 검증을 대신하지 않는다.

## 결정하지 않은 사항

- 실제 PDF/기관·연도별 골든 정답, 공시 범용화 정확도
- 운영 LLM 모델, 데이터 외부 전송 허용, 비용 한도
- 운영 retention, 사용자 인증, 승인자 및 review UI
- PostgreSQL business key/동시성/소스 우선순위 (Phase 5)
- 불완전 그리드·borderless table 탐지 알고리즘 (Phase 2)

외부 전송은 하지 않았다. 초기 코드는 동기식 로컬 개발용이며, 악성 PDF의 CPU/메모리 격리를
보장하는 운영 서버가 아니다. 운영에는 별도 프로세스 타임아웃·동시성 제어·인증이 필요하다.
