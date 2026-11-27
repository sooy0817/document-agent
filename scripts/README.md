# 실행 도구와 과거 실험 구분

## 현재 일반 입력에 사용하는 진입점

| 목적 | 진입점 | 입력 |
|---|---|---|
| PDF 추출·표 구조 | `python -m doc_understanding.cli` | `--pdf`, 출력 폴더, 선택 페이지 |
| 문서 JSON 적재·검색·답변 | `python -m doc_understanding.document_rag` | 저장 run/manifest, 질문 |
| 문서 목록·일괄 처리 | `python -m doc_understanding.document_rag.batch` | root/glob/제외 목록 또는 manifest |
| 로컬 검토 화면 | `nh-review` | 업로드 문서 또는 명시적 샘플 카탈로그 |
| 문서 평가 캠페인 | `python -m doc_understanding.teams.campaign` | 사용자 선정 문서·역할 또는 명시적 pilot manifest |
| 동결 검사 | `scripts/check_parser_freeze.py` | 고정 baseline을 읽기 전용 대조 |
| 하드코딩 점검 목록 | `scripts/audit_domain_assumptions.py` | 루트 소스; 선택 출력 경로 |

## 과거 실험 도구

`prepare_*`, `evaluate_*`, `report_*`, `run_*`라는 이름만으로 일반 실행 도구라고
판단하지 않는다. 남원/강구, C1, 특정 날짜의 holdout/period/axis 실험 도구는
해당 스냅샷의 표본·정답·보고서 구조를 전제로 한다. 테스트가 import하는 함수도
있어 일괄 이동하거나 삭제하지 않았다. 표본명과 기대값은 평가 데이터의 일부다.

이 도구의 고정 데이터 경로·기관명·모델은
`reports/generic-runtime-cleanup-20261109/domain-assumptions.json`의 `explicit_tool`
항목에서 파일별로 확인할 수 있다. `smoke_review_model.py`는 합성 PDF를 대상으로
한 명시적 모델 smoke 테스트다. 모델 호출 승인 없이 실행하지 않는다.

원본 PDF·저장 IR·평가 이력은 유지한다. 현재 경로의 변경 내용은
[전체 점검 문서](../docs/DOMAIN_ASSUMPTIONS_AUDIT.md)를 따른다.
