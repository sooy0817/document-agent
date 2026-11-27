# ADR-037: 원문 span과 좌표에 기반한 Native–Physical 표 연결

2026-10-27. 구현. 표 동일성/포함 관계 후보이며 의미 승인 아님.

## 문제

ADR-033의 동일 bbox(1.5pt) 조건은 별도 복원된 내부 표가 있어도 Native bbox가
다르면 찾지 못했다. Native와 Physical이 두 후보로 남아 중복 span 및 순서 모호성도
발생했다. ADR-036의 재분리로 이미 독립된 격자를 다시 만드는 것은 이 문제의 해결이 아니다.

## 공통 규칙

`table_understanding/table_identity.py`에서 같은 페이지의 두 표현을 비교한다.

1. 동일 원문 span ID의 **비공백 집합이 완전히 같아야 한다**. 문자열 유사도나
   숫자 동일성으로 대신하지 않는다. 존재하지 않는 ID, 부분 겹침, 공백뿐인 표는 거절한다.
2. 공통 면적/각 bbox 면적이 양쪽 모두 0.8 이상이어야 한다. 큰 바깥 표와 작은 내부
   표의 혼동 방지용이다. 이 비율만으로 연결하지 않는다.
3. 각 span 중심이 해당 표현의 표 bbox 및 배정 셀 bbox 안에 있어야 한다.
   기존 좌표 허용 오차 1.5pt는 그대로다. 비공백 span이 같은 표현의 여러 셀에
   중복 배정되면 거절한다.
4. 양방향 일대일 대응이어야 한다. 하나의 Native에 여러 Physical, 또는 그 반대는
   선택하지 않고 보류한다. 경쟁 Physical이 fallback에서 확정되는 것도 막는다.
5. 확정된 대응은 하나의 child 참조로만 다룬다. Native/Physical bbox의 합집합이
   동일한 부모 셀 안에 들어가야 하며, child span은 부모 span에 포함돼야 한다.
6. 기존 읽기 순서·누락/중복/부모 밖 span 검사를 다시 수행한다. 표 외부 설명문이
   없어도 기존에 복원된 독립 표는 연결할 수 있다. 물리 재분리 eligibility와 구별한다.

## 보존과 적용

- 추출기, PhysicalGridReconstructor, F0, 물리 분리 단계 변경 없음.
- DocumentIR/PhysicalTableIR/StructuredTableIR/CanonicalDocument, DB 계약 변경 없음.
- `build_nested_content`, CLI `link-nested`와 `build`가 새 연결기를 사용한다.
- 기존 sidecar 계약에 `linkage_algorithm` 및 `table_identities` 감사 목록만 추가한다.
  목록에는 공유/누락/추가/알 수 없는 span ID, 좌표 충돌 ID, 양쪽 bbox 면적 커버리지,
  판정 및 보류 원인이 포함된다. 원본 데이터는 수정하지 않는다.
- 이전 필드 없는 sidecar는 `bbox/1`로 읽되 현재 알고리즘으로 몰래 재검증하지 않는다.
  검증 시 버전 오류를 알리고 새 경로에 생성하도록 한다. 기존 파일은 보존한다.
- `geometrically_linked`는 표 동일성·포함·순서 근거에 대한 상태이다. 행/열 의미,
  단위/주석의 의미 범위, 금융 fact 승인은 `not_validated`로 유지한다.

## 검증

하나은행 H04는 이제 개발 사례다. HSBC/농협 및 다른 기존 자료는 회귀이며 이번
실험을 신규 Holdout 일반화 성능으로 표시하지 않는다. 원본 출력 hash를 동결해
연결만 비교한다. 동일 숫자의 다른 span ID, 좌표가 틀린 span 배정, 부분 집합,
공백·미등록 ID, 다대일/일대다 경쟁, 이전 sidecar 호환을 음성 테스트한다.

보고서: `reports/nested-identity-20261027/comparison.html`.
