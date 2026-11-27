# ADR-054 — 담기 사례의 사람 정답 참조

- Date: 2026-11-03
- Status: Implemented, local review only

기존 error-collection/1.0 snapshot과 feedback event는 수정하지 않는다.
human-reference/1.0 전체 참조 버전을 새 reference event로 추가한다.
각 버전은 parent_revision과 UUID를 갖고, 저장 시 이전 버전 일치를 검사한다.
원본 PDF/Canonical 해시가 수집 당시와 다르면 새 참조 저장을 거절한다.

참조는 역할 수정과 방향 있는 연결/연결 금지 주장을 여러 개 포함한다.
각 주장은 원본 셀·표·span ID, 원문 인용, 이유와 proposed/uncertain 상태를 갖는다.
셀 역할은 cell/span만 대상으로 한다. 추출 누락은 페이지 위치를 근거로 기록할 수
있지만 존재하지 않는 셀 ID는 만들지 않는다. 서버가 선택된 근거의 페이지·bbox·원문을
참조 버전 안에 보존한다. 인용 일치 검사는 의미 정답 승인이 아니다.

proposed만 있으면 reference_draft, uncertain만 있으면 uncertain, 혼합이면
partial_reference로 표시한다. approval=not_adjudicated, training_ready=false를
서버에서 고정한다. 기존 snapshot의 unreviewed 상태는 역사적 값으로 유지한다.
원본 IR, CanonicalDocument, DB, 자동 추론/학습 경로는 변경하지 않는다.

현재 UI는 원본 페이지 위 실제 추출 bbox와 목록으로 셀/표/span을 고른다.
임의 bbox 그리기나 미추출 셀 생성은 범위 밖이다. 페이지 전체 근거와 위치 설명을
사용한다. 문서/양식 단위 Holdout 분리는 추후 데이터셋 관리에서 별도 수행한다.
ZIP에는 현재 참조, 이전 참조 이력, 근거 원문·좌표가 함께 포함된다.
