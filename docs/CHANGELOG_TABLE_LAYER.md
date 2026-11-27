# 변경점 요약 — 2026-10-16

## 추가 파일
- `docs/TABLE_STRUCTURE_UNDERSTANDING.md`: 독립 복합표 레이어 상세 설계, 입출력 계약, 단계별 알고리즘, 상태 전이, 오류 코드, 테스트, 저장 영향.
- `docs/ADR/003-table-structure-layer.md`: 기존 역할 분산의 문제, 선택한 독립 모듈과 대안/트레이드오프 기록.
- `.agent/agents/table-structure-engineer.md`: 구현 담당 에이전트 범위와 금지사항, 완료 조건.

## 수정 파일
- `docs/DESIGN.md`: diagram에 레이어 삽입; 패키지 구조에 `table_understanding/` 추가; matcher 입력 및 Phase B2 연결.
- `.agent/AGENTS.md`: 필수 설계 읽기, 독립 IR 경계, LLM patch 제한, 단계 우선순위 보강.
- `.agent/workflows/implementation-plan.md`: Stage 1.5 추가 (템플릿 매처보다 먼저 구현).
- `README.md`: 탐색 순서·신규 파일 링크·개념적 흐름 갱신.

## 영향 없는 항목
기존 수집기와 파서 MVP 코드를 ZIP에서 수정하지 않음. DB 마이그레이션을 실행하지 않음. `configs` 예시 표준 템플릿 JSON 변경 없음. 실제 소스 코드 구현/정확도 검증 미수행.

## 핵심 인터페이스 변경
Before: `DocumentIR -> segmentation -> TemplateMatcher -> Interpreter`.
After: `DocumentIR -> segmentation -> TableStructureAnalyzer(PhysicalTableIR => StructuredTableIR) -> TemplateMatcher -> Interpreter`.

분석 실패는 원본과 `partial/review_required` 상태로 전달하며 자동 Fact 적재하지 않는다. Matcher 템플릿은 재해석 힌트일 뿐 source 구조를 덮어쓸 수 없다.
