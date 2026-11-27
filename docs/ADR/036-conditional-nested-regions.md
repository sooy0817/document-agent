# ADR-036: 조건부 중첩 물리 격자 분리

2026-10-27. 상태: 구현, 제한된 개발/동결 평가. 의미 승인 아님.

## 문제와 결정

F0의 stroke/fill 선을 평면 격자로 복원하면 바깥 셀 내부의 작은 표 경계도
바깥 표의 행/열 후보가 된다. Native DocumentIR에서 내부 표를 찾았더라도
단일 PhysicalTableIR로 합쳐져 ADR-033의 셀 내용 연결이 보류되는 사례가 있었다.

`table_understanding/nested_regions.py`는 기존 flat 결과 다음에 실행한다.
DocumentIR에 독립 내부 표 후보가 있고, 그 bbox를 포함하는 유일한 최소 Native
부모 셀이 있으며, 내부 표 밖 설명 span과 좌우 여백이 있을 때만 분리를 시도한다.
파일명, 기관명, 페이지, 기대 행/열 수는 조건에 사용하지 않는다.

내부 영역에는 기존 선을 잘라 전달하고, 부모 영역에서는 내부 전용 선 구간만
제외한다. 부모 셀과 공유하는 경계는 남긴다. 기존 PhysicalGridReconstructor로
양쪽을 각각 재구성한다. Native 내부 셀 기하, span 집합, 복원된 부모 bbox,
영역 밖 셀의 문자열/기하가 보존되지 않으면 해당 flat 영역 전체를 원복한다.

## 계약과 적용 경로

- PhysicalTableIR 2.1, StructuredTableIR, CanonicalDocument 필드 변경 없음.
- 원본 DocumentIR 및 이전 실행 결과 변경 없음.
- 후보가 있는 페이지의 변경 전 `page-NNN-flat.json` 보존.
- 시도/거절/새 ID/선 참조는 `page-NNN-nested.json`에 기록.
- 분리된 표의 상태는 partial/inferred 및 NESTED_REGION_HYPOTHESIS.
- `eval.physical.evaluate_pdf`에서 기본 실행하며 staging/기존 배치가 재사용.
- physical-grid 및 staging의 `--no-nested-regions`로 분리 단계 비활성화 가능.
- 낮은 수준의 reconstructor와 독립 S0/S1 실험은 변경 없음.
- CanonicalDocument 생성 후 기존 ADR-033의 `build_nested_content` 및
  `resolve_cell_content`로 부모 Native 셀 ID → 내부 Physical 표 ID와 설명 순서를
  별도 파생 JSON으로 조회한다. 배치가 이 sidecar를 자동 저장하는 것은 아니며,
  이번 평가 스크립트에서 생성/검증/저장한다. 원본 JSON을 대체하지 않는다.
- 부모 셀 원문에는 내부 표 문자열도 남는다. 조회용 items에서는 span을 분리해
  중복 여부를 검사한다. 부모 원문 삭제나 RAG 청킹 정책 변경은 하지 않았다.

## 제한

Native 내부 표 후보가 없으면 실행하지 않는다. 설명문 없는 내부 표, 좌우 여백 없는
구조, 겹치거나 다단으로 중첩되는 후보, 불완전 경계는 보류한다. Native 합의는
정답 증명이 아니므로 원본 이미지 평가를 별도로 수행한다. 페이지를 넘는 내부 표
조각 연결, 헤더 의미 및 단위 해석, 도식 인식, LLM/VLM은 이번 범위 밖이다.

## 검증과 보존

HSBC는 개발 데이터다. 강동농협 기존 source-corrected Gold를 수정하지 않고
회귀 평가한다. 새 한글 PDF는 구현 hash 동결 후 원본으로 선정하고 결과 확인 후
알고리즘/테스트를 수정하지 않는다. 실패와 제외 자료도 기록한다.

실제 결과 및 원본/IR 비교: `reports/nested-region-split-20261027/comparison.html`.
동결 파일: `data/nested-region-split/v1/implementation.frozen.json`.
