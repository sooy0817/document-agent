# ADR-005 — Phase 2-A 물리 격자 복원

- 날짜: 2026-10-16
- 상태: 구현 결정. 의미 Row/Column Tree는 이 결정의 범위 밖.

## 입력과 경계

원본 DocumentIR, 벡터 drawing commands, 선택적 grayscale 페이지 이미지가 입력이다.
이름/숫자/템플릿은 격자·병합 판정에 사용하지 않는다. 텍스트는 격자를 만든 후 좌표로만 연결한다.
Table Region → 축방향 선분 → 실제 교점 → 경계 coverage → 연결 영역 → 직사각형 span → 검증으로 분리한다.

## 병합을 복원하는 조건

다른 열/행에서 관찰된 경계로 elementary grid를 만든다. 전체가 관찰되는 경계는 present,
선분이 일부만 존재하는 경계는 uncertain, 선분이 없는 내부 경계는 absent다. 작은 수치 오차만
tolerance로 허용하고 끊어진 긴 선을 자동 연장하지 않는다.

absent 경계를 사이에 둔 slot을 연결하되 결과가 직사각형이고 전체 외곽이 present이며 내부
경계에 present/uncertain 선분이 없을 때만 rowspan/colspan을 확정한다. 이 조건을 만족하지
못하면 셀의 후보 bbox와 원문을 유지하고 span=null, status=unresolved다.
완전히 삭제된 선과 의도적인 병합은 영상만으로 구분 불가능할 수 있다. 출력은 관찰된 도형에
일관된 물리 복원이며 원래 편집기의 병합 속성을 증명하는 것은 아니다.

## 계약

기존 PhysicalTableIR은 미사용 초안이므로 physical-table-ir/2.0으로 버전을 올린다.
단일 페이지/영역별 table_id, page_number, bounding_box, rows/columns, reconstructed cells,
source_cells, source_links, 선분/교점/경계 evidence, unresolved_boundaries, validation_issues를 둔다.
원본 CellIR은 수정하지 않는다. source_cells와 원본 span refs를 보존하고 exact/overlap 링크 및
미연결 source ID를 별도 기록한다. 물리 검증 validated는 금융/의미 검증 승인 상태가 아니다.

## 보수적인 제한

선 없는 표는 명시적 region hint가 있으면 원문과 review_required 결과만 반환한다. 텍스트 배치로
임의의 격자를 만들지 않는다. 자동 탐지가 못 찾은 영역은 page-level diagnostics로 기록한다.
Raster 추출은 축방향의 충분히 긴 검은 run만 사용하며 기울어진/회전된 스캔의 deskew는 후속이다.
연속 페이지 조립, semantic panels, Row Tree/Column Tree, Matcher, LLM, DB 적재는 미구현으로 유지한다.

## 실제 문서 평가 보완 — physical-grid/0.1.1

강구농협 전체 38페이지 평가에서 외곽 세로선이 없는 표의 좌우 열이 제외되는 문제를 확인했다.
영역 bbox는 연결된 선분의 전체 extent를 포함한다. 관찰된 격자 바깥으로 선분이 연장되면
그 끝을 후보 외곽 좌표에 추가하되, 선이 없는 경계는 absent/unresolved로 유지한다.
닫힌 내부 일부만 검증하고 전체 표 성공으로 집계하지 않는다. 의미나 페이지 번호로 보정하지 않는다.
JSON 계약은 2.0 유지, analyzer_version만 0.1.1로 변경한다.
실제 평가와 픽셀 전용 모사 평가의 제한은 reports/GANGGU_REAL_EVALUATION.md 참조.
