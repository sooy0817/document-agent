# 신규 미관측 문서 Tree 평가 설계 — 실행 전

이 문서는 남원 진단 보고서와 분리한다. 현재 신규 PDF 평가 수 0, 모델 호출 0,
일반화 성능 N/A. 남원 결과로 전국 자료 성능을 추정하지 않는다.

## 데이터 구획

- 남원축협: 개발·회귀 전용. 발견한 실패로 수정/재실행 가능, 일반화 지표 제외.
- 강구농협 및 이미 본 기관: 기존 개발 노출 목록으로 제외.
- 남평농협: 최종 Holdout. 현재 열람·추출·샘플링·튜닝 모두 제외.
- 신규 약 20개 PDF: 별도 미관측 기관 평가. 평가 결과를 보고 튜닝하면 이 묶음은
  개발셋으로 전환하고 이후 일반화 평가에는 다른 기관 묶음을 새로 뽑는다.

## 사전 inventory 계약

기관 별칭을 canonical institution_id로 정리한 메타데이터 JSON 배열을 준비한다.
필수: document_id, institution_id, institution_name, period(YYYY 또는 YYYY-MM),
document_type(정기/요약/감사/재무제표/별첨 등), format_family(양식군),
native_text_group(native/scanned/mixed/unknown), source_path, sha256,
duplicate_cluster. prior_exposure도 기록한다. 소스 파일 hash는 실제 원본 bytes로
만들어야 한다. 이번 도구는 제공된 메타데이터만 읽고 PDF를 열거나 hash를 계산하지 않는다.
실행 전 선택된 파일만 byte hash를 재확인하며 불일치는 실행 중단한다.

## 누수 차단

동일 기관+기간의 정기·요약·별첨, 동일 SHA, 내용 중복 cluster를 union-find로
하나의 leakage group으로 연결한다. 중복 cluster는 재무제표 페이지 이미지/텍스트
지문과 수동 중복 검토를 통해 사전에 부여한다. 서로 다른 파일이라고 독립 표본으로
간주하지 않는다. 중복 정보가 없으면 자동 통과시키지 말고 목록 구축을 보류한다.
금액이 가려진 텍스트 유사도만으로 동일 내용 여부를 확정하지 않는다.

제외 기관 또는 prior_exposure가 하나라도 있으면 그 전체 leakage component를 제외.
선택은 기관당 최대 1 PDF, leakage group당 최대 1 PDF로 제한한다. 선택하지 않은
동일 component의 다른 파일도 train/dev로 섞지 않고 같은 평가 partition에 예약한다.

## 재현 가능한 선택

Seed **20261019**, 목표20. document_id로 입력 정렬 →
기간 × 문서 유형 × 양식군 × native/scanned 구획 → strata마다 seeded shuffle →
strata 순서 seeded shuffle → round-robin으로 균형 추출한다.
이는 전국 모집단 비율 추정이 아니라 양식 다양성을 확보하는 균형 진단 표본이다.
희소 strata의 수량 부족은 shortfall로 보고, 편한 문서로 임의 교체하지 않는다.
seed·inventory hash·freeze hash·선택 문서 SHA·기관·기간·양식·누수그룹을 저장한다.

도구:
`python -m doc_understanding.eval.sample_unseen inventory.json resolver-freeze.json selected.json --seed 20261019 --count 20`

이번에는 합성 메타데이터로만 추출 도구를 테스트했다. 실제 전국 inventory 또는
선택된20개 PDF manifest가 생성됐다고 주장하지 않는다.

## 문서 내 표 선정 (모델 결과 보기 전)

1. 소스 렌더링/원문만 보고 전체 표 후보를 수기 inventory로 작성한다. 자동 detector가
   못 찾은 표도 후보에 넣어 탐지 성공한 표만 평가하는 편향을 방지한다.
2. normal: 단일 헤더, 병합 없는 규칙 격자; complex: 다단헤더/그룹/병합/병렬/연속;
   high-risk: 희미한 경계/무테/회전/스캔/다중 단위/범위가 모호한 주석으로 구분.
   복합/위험이 겹치면 high-risk 우선으로 bucket을 고정한다.
3. 문서마다 bucket별 최대2개를 seed+document_sha로 결정적으로 선택(최대6개).
   없는 bucket은 N/A, 실패한 표를 정상표로 교체하지 않는다. OCR 미지원은 별도
   applicability/coverage로 보고, native-only semantic 성능에 섞지 않는다.
4. 실제 연속표는 인접 페이지를 하나의 logical-table 평가 단위로 포함한다. 동일 주석의
   연도 비교 표와 단순 row continuation을 별개 label로 작성한다.
5. 2명의 원본 검토자가 예측을 보지 않고 주석을 작성하고 불일치를 조정한다.
   각 annotation의 작성자·검토자·버전·원본 hash·검토 상태를 남긴다.

## 동결과 보고

평가 결과를 열기 전에 Resolver source hash, 설정, physical/semantic schema 버전,
모델 snapshot, prompt/context 버전, dependency 목록, evaluator/annotation 버전을 동결.
현재 snapshot은 `data/tree-phase3a/resolver-freeze.json`; 변경하면 새 snapshot/새 run이다.
누락/수정 금지, 모든 실패와 기권 포함. LLM을 사용할 때는 별도 승인/비용상한이 필요하다.

Parent P/R, path, cell mapping, aggregation, continuation, evidence를 문서별·stratum별로
보고하고 micro와 기관 단위 macro를 같이 제시한다. 표/셀을 독립 기관처럼 취급하지 않는다.
기관 bootstrap 신뢰구간과 적용 가능 분모, 실패 문서, 미지원 범위를 함께 보고한다.
최종 남평 평가는 새 문서 결과를 보고 확정한 최종 설정을 고정한 뒤 한 번 수행한다.

## 현재 상태

설계/추출 도구 준비. 전국 원본 미실행, 남평 미사용, 일반화 성능 N/A.
