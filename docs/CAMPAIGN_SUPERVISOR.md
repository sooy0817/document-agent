# 캠페인 Supervisor 1차

로컬 문서를 개발·회귀·holdout 표본으로 등록하고, 모델 호출 없이 LangGraph Supervisor가 개발·회귀 문서를 기존 문서 처리기로 순차 실행한다. 각 표본의 SHA-256, 상태, 결과 경로, Supervisor 결정 이력을 별도 SQLite에 저장한다. 같은 문서를 서로 다른 역할로 중복 등록할 수 없으며 등록 이후 원본이 바뀌면 처리를 차단한다. `completed`는 처리 완료이며 정확도 검증이나 의미 승인이 아니다.

프로젝트 루트에서 가상환경과 그래프 의존성이 설치된 경우:

```powershell
.venv/Scripts/python.exe -B -m doc_understanding.teams.campaign create "병합 표 구조 평가"
# 출력된 campaign_id를 아래 명령에 넣는다.
.venv/Scripts/python.exe -B -m doc_understanding.teams.campaign add <campaign_id> reports/physical-fixtures/horizontal.pdf --role development
.venv/Scripts/python.exe -B -m doc_understanding.teams.campaign run <campaign_id>
.venv/Scripts/python.exe -B -m doc_understanding.teams.campaign show <campaign_id>
```

실행 결과는 기본적으로 `work/campaigns/`에 저장된다. 원본 경로는 로컬 SQLite에 기록되며 처리 시 해시 확인 후 별도 작업 디렉터리에 복사한다. 한 캠페인에 최대 20개를 등록할 수 있다. holdout은 등록만 하고 현재 실행하지 않는다. 중단된 실행은 자동 재개하지 않으며 상태가 running으로 남을 수 있다.

현재 Supervisor는 규칙 기반이다. 에이전트 검토팀·파서 수정·기준 대비 후보 평가·새 자료 수집·모델 의사결정·영속형 LangGraph 체크포인트·중단 후 재개·캠페인 화면은 아직 연결하지 않았다. 모든 문서가 처리돼도 `review_not_connected`에서 멈춘다. 기존 사용자 검토 큐나 원본 데이터는 변경하지 않는다.

## 자동 평가 1차: 로컬 표본 발견과 화면

문서 탐색기의 자동 평가 시작 버튼은 `data/campaign-inbox/` 바로 아래 PDF를 최대 20개 발견해 개발 표본으로 등록하고 기존 문서 처리기를 실행한다. 하위 폴더와 심볼릭 링크는 따라가지 않으며 동일 내용은 중복 등록하지 않는다. 기존 데이터셋이나 holdout을 자동 탐색하지 않는다. SQLite에 발견·처리·감독 판단을 기록하고 구조 검토 연결 지점에서 대기한다. 수집함이 비어 있으면 표본 없음으로 멈춘다. 외부 수집, 자동 검토, 파서 개선, 전후 비교와 holdout 평가, 재시작 후 자동 복구는 아직 구현되지 않았다. 개발 표본의 처리 완료는 정확도 검증이나 사실 확정이 아니다.

## 2026-11-04 업데이트: 자동 점검 연결

위의 이전 단계 설명 중 `review_not_connected`에서 중단한다는 내용은 현재 버전에 해당하지 않는다. 현재는 개발·회귀 문서를 처리한 후 자동 점검을 실행하고 `specialist_review_not_connected`에서 중단한다. 자동 점검은 OCR 필요, 미검증 표와 불확실한 셀 경로를 기록한다. 이는 전문 에이전트 검토나 정확도 검증이 아니다. 상세한 실제 구성은 `CAMPAIGN_TEAM_MAP.md`를 따른다.
