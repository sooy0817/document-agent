> 과거 실험 기록 — 2026-11-09 이 문서의 실행 모듈과 `/metrics`는 활성 코드에서 제거했다. 아래 명령과 데이터 경로는 당시 기록이며 현재 실행 안내가 아니다. 복구 정보는 [개발 안내](DEVELOPMENT.md#과거-코드자료를-확인할-때)를 참조한다. 원본 데이터·DB·검토 이력은 보존했다.

# MetricCandidate MVP — 5개 개발 문서

## 무엇이 달라졌는가

라벨 검색은 검토 위치를 찾는 Baseline으로만 사용한다. 동일한 라벨이라도 표의 목적,
상위 경로, 원본 셀 연결, 기간·금액 헤더, 기존 구조/설명 참조 정합성에 따라 제안 또는
ABSTAIN으로 달라진다. 기존 Rule Tree와 의미 설명은 미승인 후보이며 정답이 아니다.
이번 엔진은 **contextual_rule**이다. 외부 Agent/LLM과 Hybrid는 실행하지 않았다.

## 다섯 지표의 정의

| 코드 | 의미 | 기간 | 제외 또는 별도 검토 |
|---|---|---|---|
| total_assets | 표의 회계범위 전체 자산 총액 | 시점 | 총자산순이익률, 평균자산, 자산 하위 구성 |
| total_liabilities | 자본을 제외한 부채 전체 | 시점 | 부채+자본 합산, 부채 비율, 하위 차입금 |
| total_equity | 회계범위의 자본 총계 | 시점 | 부채 및 자본 총계, 출자금만의 금액 |
| operating_revenue | 해당 손익 범위 전체 영업수익 | 기간 | 신용·경제·기타 수익 구성항목, 영업외수익 |
| net_income | 해당 기간 세후 최종 순손익 | 기간 | 세전, 계속사업만의 손익, 주당/좌당, 수익률 |

이 정의와 원문 표의 회계기준이 호환되는지는 추가 의미 검토 대상이다.
자본변동표·주석에 나타난 순손익도 실제 순손익일 수 있다. 이를 오류라고 단정하지 않고
기간·중복 열·본문 연결이 부족하면 ABSTAIN한다.

## 처리 흐름과 기존 계약 재사용

1. 기존 5개 Staging run의 DocumentIR 및 Physical/Structured Artifact를 읽는다.
2. 라벨 위치를 찾고 같은 행 셀, 헤더의 가로 범위, 주변 span/본문·단위·주석을 수집한다.
3. 원본 SHA, 페이지, 셀 위치·텍스트로 기존 StructureProposal/Description을 연결한다.
   현재 강동·개성인삼에는 해당 산출물이 있지만 다른 문서/행에는 없는 부분이 있다.
4. 지표 정체성(PROPOSE / OTHER_METRIC / ABSTAIN)을 판단한다.
   원본 셀에 연결된 Tree의 부모 경로가 같은 전체 지표의 하위임을 시사하면 보류한다.
5. 값 바인딩은 별도로 처리한다. 구성비/증감은 절대 금액 후보에서 제외한다.
   년도만 있으면 관측 연도만 보존하며 임의로 연말이나 연간 시작일을 만들지 않는다.
   제54기 같은 기수는 원문 날짜 줄과 같은 기수로 연결한다.
6. 기존 MetricCandidate 0.1에 원문 값, Decimal 문자값, 단위 배수, 기간·범위 근거를 담는다.
   손실 전용 행의 양수 표기는 부호 해석 미확정으로 남긴다. 공란/대시는 0이 아니다.
7. 별도 MetricMappingBundle이 후보·보류·문맥과 원본 참조를 묶는다.
8. 파생 processing_run과 artifact로 Staging에 추가하고 원본 증거 DB와 대조한다.
   기존 추출 run의 Artifact 집합, 리뷰 이벤트, Gold, Fact는 변경하지 않는다.

현재의 한계: 상위 경로가 비어 있거나 모호한 경우 완전한 의미 계층 판정을 못 한다.
본문의 장거리 소속·각주 범위·페이지 연속 상속을 확정하지 않는다. 단락만의 지표는 자동
구조화하지 않는다. 라벨 검색에 걸리지 않은 실제 지표의 전체 재현율은 아직 N/A다.
설명 참조 정합성 검사는 설명 문장의 사실성 검증을 대신하지 않는다.

## 실행 및 저장

`python -m doc_understanding.metric_mapping.run <new-output-directory>`

`python -m doc_understanding.metric_mapping.evaluate <data-directory> <report-directory>`

`python -m doc_understanding.metric_mapping.store <data-directory> <sql-result.json>`

`python -m doc_understanding.metric_mapping.report <data-directory> <report-directory>`

최종 데이터는 data/metric-mvp-v3이다. v1/v2는 개발 중간 실행이며 그대로 보존한다.
파생 실행은 source run + payload SHA로 중복 방지한다. 다른 해석 결과는 새 버전 실행이다.
DB의 JSONB rows 배열은 모든 제안/보류를 포함한다. candidates 배열은 값 후보만 포함한다.
동일 원문 지표의 반복 공시·주석 중복을 합쳐 발행하는 기능은 구현하지 않았다.

## 평가를 해석하는 방법

18개 행은 원본 PDF/Native IR에 근거한 보조 개발 정답이며 독립 사람 검토 전이다.
강동 p2·p3와 강구 p9는 원본 렌더링을 직접 대조했다. 다른 행의 판정은 원문 IR 기반
보조 검토로서 독립 Gold로 부르지 않는다. 원본 수치 바인딩 표본 10개는 강동 p2·p3에 한정한다.
정체성 Precision/Recall과 금액·표시 연도·단위 바인딩을 별도 계산한다.
ABSTAIN은 오매핑은 아니지만 정답 지표에 대한 Recall 분자에는 들어가지 않는다.
Agent/Hybrid는 동일 row_id→decision 평가 입력을 사용하며 미실행이면 N/A다.
추후 값 바인딩 전체/회계범위/정확 기간·단위·부호·근거 정확도를 독립 Gold로 확대해야 한다.

## 사람 검토

기존 loopback 검토 앱의 `/metrics`에서 원본과 후보를 비교한다. 별도 임시 포트 8766으로
열어 기존 8765 앱과 검토 이력을 건드리지 않는다. 정적 HTML도 제공한다.
검토 이벤트는 브라우저 저장 + JSON 내보내기로만 보존한다. 서버 이벤트 가져오기,
Gold 후보 승격, 최종 Gold 승인과 운영 발행은 이번에 연결하지 않았다.
검토자는 원본 근거와 수정 전후·날짜·이름을 내보내야 한다. 자동 사람 승인 이벤트는 없다.

## 다음 개발 우선순위

1. 보류된 연속 페이지 총계/손익의 제목·기간·단위 상속 근거.
2. 이익/손실 대체 행과 숫자 부호, 여러 열의 중복 금액 처리.
3. 기관명 및 통합/신용/일반의 문서 전체 적용 범위와 충돌 검사.
4. 독립 사람 검토 정답 확정 후 Rule/Agent/Hybrid 동일 데이터 비교.
5. 별도 승인 뒤에만 검증된 Financial Fact 발행 연결.
