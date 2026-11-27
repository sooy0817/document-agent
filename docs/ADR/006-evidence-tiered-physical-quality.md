# ADR-006 — Phase 2-A 증거 수준과 실제 문서 평가

상태: 구현. Phase 2-B 및 의미 구조는 범위 밖. ADR-005의 단일 present/absent 판정을 확장한다.

## PhysicalTableIR 2.1 / analyzer 0.2.0

- Boundary status에 inferred를 추가한다. evidence_level은 observed, small_gap_bridged,
  endpoint_alignment, fill_transition, text_conflict, missing이다. 원래 coverage는 보정하지 않는다.
- PhysicalCell status에 inferred를 추가한다. 관찰되거나 증거가 명시된 추정 경계로 닫힌
  직사각형만 rowspan/colspan을 기록한다. 내부 경계는 absent여야 한다.
- inferred 셀은 확정 셀과 구별하며 표는 partial이다. unresolved의 span은 계속 null이다.
  inferred도 unresolved_boundaries 검토 목록에 보존한다(이 목록은 경계 검토 대상으로 해석).
- 같은 위치의 선분 구간을 합쳐 반복 정렬을 평가하되 원본 line IDs와 실제 coverage를 유지한다.
  최대 2pt, coverage 94% 이상인 작은 끊김만 텍스트 bbox 충돌이 없을 때 연결한다.
  긴 공백과 완전히 없는 내부 경계를 연장하지 않는다.
- 외곽선이 없으면 3개 이상 독립 좌표의 선 끝점이 맞고 텍스트 bbox를 가르지 않을 때만
  endpoint_alignment 후보를 생성한다. 후보는 관찰된 선으로 승격하지 않는다.
- 그림/조직도 음성 규칙은 연속 선 coverage, 행·열 후보 수, 반복 텍스트 x정렬,
  숫자형 span 비율, 작은 도형 크기를 함께 사용한다. 제목/항목/페이지 ID는 사용하지 않는다.
  거부된 region의 bbox, 판단 특징, 원본 선분을 region_assessments에 보존한다.
- 픽셀 경로는 수직 방향 양쪽 이웃과의 대비로 얇은 어두운/밝은 선을 검출한다.
  균일한 진한 배경은 stroke가 아니다. 긴 색상 전환은 fill_transition 추정 증거로 분리한다.
  legacy_dark / adaptive_dark / adaptive_contrast를 비교할 수 있다. OCR은 없다.

기존 2.0 JSON은 과거 실행 기록으로 유지한다. 새 출력 소비자는 inferred 상태를 처리해야 한다.
DB 마이그레이션, 템플릿, 의미 Row/Column Tree는 추가하지 않았다.

## 골든 및 평가 원칙

tests/golden/ganggu-physical-v1.json은 원본 120 DPI PNG의 수기 좌표와 병합 정의를 확장한
에이전트 시각 주석이다. 220 DPI 확대 원본으로 두 애매한 부분을 재검토했다. 예측 JSON을
읽어 생성하지 않는다. 작성자는 이전 예측 결과를 보았으므로 blinded holdout은 아니다.
독립 사람 검토는 pending이며, 이 데이터로 일반화 정확도를 입증했다고 주장하지 않는다.

변경 전/후 모두 같은 최종 골든 SHA-256과 같은 원본 PDF를 사용한다. 검출 평가는 완전하게
주석한 4페이지에만 적용한다. 나머지 34페이지의 전체 precision/recall은 N/A이다.
관찰 경계만의 지표와 추정 경계를 포함한 지표, resolved/inferred/unresolved 비율을 분리한다.
개발 중 골든 수정은 원본 이미지 근거와 수정 이력을 남기고 모든 비교를 다시 계산한다.

결과: reports/PHASE2A_QUALITY_IMPROVEMENT.md.
