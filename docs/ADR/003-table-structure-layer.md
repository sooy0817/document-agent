# ADR-003 — Independent Table Structure Understanding Layer

- Status: Proposed
- Date: 2026-10-16
- Supersedes: none; extends `docs/DESIGN.md` sections 3, 4.2, 5.3, 6, 9, 11 and roadmap

## Context
기존 설계의 표 후보 추출/분할, `Table IR`, `Template Matcher`와 LLM Interpreter에 복합표 해석 책임이 흩어져 있었다. 고정 농협 양식과 범용 PDF의 공통분모인 **물리/의미 구조 복원**을 명시적으로 독립해야 구조 오인식의 원인과 품질을 평가할 수 있다. 예: 다단 예적금 p18, 자산/부채 병렬표, 손익계산서 연속표, 주석 혼재.

## Decision
1. `src/doc_understanding/table_understanding/`을 독립 패키지로 추가한다.
2. segmenter output + physical candidates를 받아 physical grid / panels / feature classifier / header-tree / row-tree / note linkage / validators를 수행한다.
3. **표 구조 판단과 템플릿 동일성 판단을 분리한다**. `StructuredTableIR`는 농협 전용 지표를 담지 않는다.
4. 매칭 전 구조 분석을 시행하되 matcher는 비파괴 힌트로 최대 1회 국소 재해석을 요청할 수 있다.
5. 경로 provenance 없는 매핑이나 숫자는 publish 금지; ambiguous/partial를 나타내며 human review로 에스컬레이션.
6. Agent는 숫자 원문 수정 불가, LLM이 생성한 patch는 원본 cell-id membership 체크 등 검증 필수.

## Consequences
- 장점: 재사용성, 표준 양식과 새로운 형식 공존, 회귀 테스트 분리, 구조 관련 장애 위치 파악.
- 비용: `StructuredTableIR` 별도 계약/버전, migration 및 실행 이력, 파서 adapter overhead.
- 위험: 기하/셀 복원 품질이 낮으면 잘못된 구조 가설 형성. 대응: source traceability, ambiguity 기록, scan fallback, human gate.

## Alternatives considered
- Matcher와 LLM Interpreter만 확장: 구현 빠르지만 vendor-specific 템플릿에 구조 로직 종속.
- 모든 페이지 Vision LLM: 고비용, 셀 추적과 숫자 정확성 문제.
- 모든 복합표 농협 수기 템플릿: 정형성에 유리하지만 비정형 문서 범용성 부족.

## Acceptance
- 상세 계약: `../TABLE_STRUCTURE_UNDERSTANDING.md`.
- 결정적 매처는 `StructuredTableIR`만 요구하며 원본 `PhysicalTableIR`는 증거로 참조.
- 실제 코드/DB/테스트 구현은 별도 단계이며 이 ADR은 구현 완료 선언이 아니다.
