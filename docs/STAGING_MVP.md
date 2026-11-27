# 문서 전체 Staging MVP

## 구현 범위

DocumentIR → DocumentLayoutProjection sidecar → 로컬 PostgreSQL staging.
PhysicalTableIR 2.1/StructuredTableIR은 기존 엔진으로 생성한 artifact로 재사용한다.
원본 PDF/IR/승인 이력의 필드와 내용을 변경하지 않는다.
Layout의 페이지 소속은 물리적 포함 관계다. 장·절 의미 소속을 확정하지 않는다.
본문/표 중복은 공유 span 참조로 보존한다. 추후 EvidenceBundle의 context adapter는
이 문서 블록을 입력으로 사용할 수 있으나 자동 Agent 연결은 이번에 실행하지 않는다.

## 로컬 실행

`python -m doc_understanding.staging.prepare --pdf <path> --output data/staging-mvp`

`python -m doc_understanding.staging.verify --manifest <run>/staging-manifest.json --report <new-report-dir>`

`python -m doc_understanding.staging.report <new-report-dir>/result.json`

prepare는 전체 페이지 추출 및 기존 표 엔진을 재사용한다. verify는 프로젝트 Docker
Compose의 nh_document DB만 사용한다. 새 패키지 설치 없이 psql stdin으로 전달한다.
SQL 문자열 인용과 JSON 인코딩을 분리한다. 자격증명은 출력하거나 인자로 전달하지 않는다.
Alembic revision도 제공하지만 이 환경에는 Alembic이 없으므로 동일 SQL migration을
transaction 안에서 적용하고 staging.schema_revision에 기록했다.

## 저장·복구 계약

source_document(path) → document_version(hash) → processing_run →
artifact(JSONB) / page / evidence / content_block / relation_candidate.
페이지·블록·span·셀은 run 안에서 FK로 연결된다. 원본 PDF bytes는 DB에 넣지 않는다.
원본 바이트 보존은 로컬 원본 해시와 DocumentIR JSONB 동등성으로 검사한다.
JSONB는 공백/키 순서의 byte 표현을 보존하지 않으므로 원본 artifact 파일과 SHA도 유지한다.

성공 요청 키 재실행은 source/version 및 모든 artifact SHA가 같을 때 no-op이다.
다른 내용으로 같은 키를 사용하면 충돌로 거부한다. 새 엔진/정책의 재처리는 새로운 key/run.
실패한 artifact transaction은 모두 rollback하며 FAILED 실행 이력은 유지한다.
실패 시 새 attempt key로 재시도한다. 프로세스 강제 종료 후 RUNNING은 자동 성공 처리하지
않고 운영자 확인 후 새 attempt로 재처리한다. 자동 stale-run 회수/작업 큐는 아직 미구현이다.
동시 같은 key는 DB UNIQUE가 중복을 막지만 패자 재시도 UX는 후속 구현이다.

## 처리 상태

페이지 Native Text는 EXTRACTED, 이미지 기반/큰 이미지와 native text의 혼합은 OCR_REQUIRED,
객체 없는 페이지는 EMPTY, sparse/vector/모호한 구성은 REVIEW_REQUIRED.
이미지 면적은 겹침을 제거한 union으로 계산한다. 사진과 스캔 텍스트를 완전히 구별하지
못하므로 진단 결과는 routing heuristic이며 정확도 검증이 아니다.
문서 상태는 OCR_REQUIRED가 최우선, 그다음 REVIEW_REQUIRED다.
혼합 문서도 native 페이지와 미처리 페이지를 별도 저장한다.
현재 임계값은 정책 0.1 고정; 신규 평가 결과를 보고 테스트 집합에 맞춰 튜닝하지 않는다.

## 1 → 5 → 40 실행 게이트

이번은 첫 Native PDF 1개만 실제 실행한다. 다음 확대 전 manifest를 확정한다.
5개는 노출된 개발 자료에서 표지/본문/주석/병렬표/긴 문서 유형을 포함한다.
40개는 개발20·검증10·잠금10, seed20261020. 기관 alias 정규화 후 기관 단위로
분할하고 기간·양식군·페이지수·진단종류로 층화한다. SHA 동일 및 동일 기관·기간의
별첨/요약/정기공시와 내용 중복은 한 그룹으로 묶는다. 기존 노출 자료는 개발 전용,
남평은 모든 이번 manifest에서 제외한다. 미확인 중복 그룹은 분할 확정 전 보류한다.
잠금 자료는 코드/설정/hash 동결 후 한 번 평가하며 실패를 보고해도 개발 디버깅에 사용하지 않는다.
실제 40개 manifest는 아직 생성/실행하지 않았다.

각 gate: 원본 hash 유지, 블록/셀 누락 0, FK 오류 0, 동일 key 중복 0,
원문 재조회 동일성, 실패 rollback/retry 통과, 페이지별 미처리 상태 누락 0.
표 partial은 저장 실패가 아니다. 구조/의미 정확도와 적재 품질은 별도 분모로 보고한다.
정확한 원문 텍스트가 있다고 금융지표가 검증된 것은 아니다.

## 후속 OCR 6개 계획

Native 40개 이후 별도 6개: 선명 스캔2·저해상도/기울기2·Native+스캔 혼합2.
개발/검증/잠금 및 남평과 기관·원문 중복이 없도록 선정한다.
먼저 6개 전체 페이지를 사람이 라우팅 표기하고 진단 precision/recall을 측정한다.
OCR 엔진/라이선스/로컬 리소스는 별도 검토 후 도입한다. 이번에는 도입하지 않는다.
새 extraction run에서 공통 DocumentIR을 생성하고 OCR provenance sidecar에
원본 hash/page/bbox/transform/engine/version/confidence를 기록한다.
사람 전사 표본으로 CER, 숫자 exact match, bbox/cell mapping, 표 누락을 측정한다.
OCR_REQUIRED를 해소하지 못한 페이지는 완료율 분모에는 남기되 성공 분자에는 넣지 않는다.
현 DocumentIR의 confidence=None 계약을 바꾸지 않고 sidecar로 calibration을 보존한다.

## 미구현 / 후속

장·절 복원, 본문/각주 범위 의미 승인, Financial Fact 정형화/발행, OCR 엔진,
Staging→Canonical 검토 승계, 운영 DB, RAG, 자동 배치 큐는 미구현 또는 후속이다.
현재 데이터는 미승인 extraction 후보이며 DB에 발행 가능한 사실로 표시하지 않는다.

이번 실패 주입은 DB artifact transaction 경계에 한정한다. PDF 추출 단계 실패는 기존
extract_ir의 failure.json으로 보존되지만 DB processing_run으로 자동 승계하는 기능은
아직 없다. 프로세스 강제 종료/디스크 부족/네트워크 단절 복구는 추가 게이트로 시험해야 한다.
따라서 1개 성공만으로 곧바로 40개 무인 배치의 준비 완료라고 판단하지 않는다.
