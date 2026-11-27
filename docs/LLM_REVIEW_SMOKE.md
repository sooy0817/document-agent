# 합성 표 LLM 검토 시험

`scripts/smoke_review_model.py`는 프로젝트의 합성 표 PDF 한 건으로 기존 `agents_roles` 검토 큐를 실행한다. 표 역할 분류, 관계 분석, 독립 판단에 최대 3회 모델 호출을 예약하며 GPT-4.1 mini를 사용한다. 실제 문서나 검토 큐 설정은 변경하지 않는다. 출력 폴더는 새로 만들어야 하며 원본 PDF·요청·응답·실패 상태를 포함한다. 작업 폴더나 파일이 모델로 전송되는 것은 아니고, 선택된 합성 표 이미지와 근거 데이터가 모델에 전달된다. 모델 호출은 비용이 발생할 수 있다.

프로젝트 루트의 PowerShell에서 다음을 실행한다.

```powershell
$runDir = Join-Path $env:TEMP ("nh-review-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
.venv/Scripts/python.exe scripts/smoke_review_model.py --data $runDir
```

`request_status=completed`와 세 역할의 응답이 있어야 모델 검토 완료다. 출력의 `api_calls`는 호출 시도 기록이므로 성공 응답이나 실제 청구를 뜻하지 않는다. 실패나 결과 미확인은 성공 종료 코드가 아니다. 기존 불확실한 호출을 자동 재시도하지 않으므로 새 시험은 새 출력 폴더에서 시작한다.

2026-11-04 실행 환경: 합성 표 추출 및 검토 요청 생성 완료. 첫 역할 호출은 `URLError`, 큐 상태 `failed`; 모델 응답을 받지 못했다. API 호스트 DNS 조회도 실패했다. 큐 테스트의 HTTP 경로는 Windows asyncio `socket.socketpair()` 생성 중 대기하여 종료하지 못했다. 로컬 큐 8개와 모의 응답을 이용한 전문 검토 관련 검사 22개는 통과했다. 모의 검사는 실제 모델 품질을 검증하지 않는다.
