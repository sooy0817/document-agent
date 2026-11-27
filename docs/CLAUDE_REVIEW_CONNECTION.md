# Claude 연결 방법 — 아직 미연결

현재 PC에는 `claude` 명령이 설치되어 있다. 이것만으로 Folio 큐에 연결되지는 않는다.
GPT-4.1 mini는 표 역할·관계·Judge를 담당하고, Claude에는 오류 결과와 관련 코드를 읽어
원인과 개선안을 작성하는 별도 역할을 맡기는 구성이 적합하다.

## 연결 순서

1. 별도 작업자 환경에 `claude-agent-sdk`를 설치한다. 프로젝트에서 사용할 경우
   `.venv\Scripts\python.exe -m pip install claude-agent-sdk`.
2. Anthropic Console의 API 키를 작업자 환경 변수 `ANTHROPIC_API_KEY`에 설정한다.
   키를 채팅·Git·로그로 보내지 않는다. SDK는 `.env`를 자동으로 읽지 않으므로
   작업자 시작 시 명시적으로 로드한다. 키 입력만으로 아래 단계가 자동 구현되지는 않는다.
3. 검토 큐의 요청 ID를 받는 Claude 작업자를 추가한다. 최소 원본 근거와 관련 코드만
   별도 작업 폴더에 전달하고, 읽기 도구만 제공한다. 도구 허용 목록만 믿지 않고
   쓰기/셸 도구 거부와 읽기 전용 작업 폴더를 함께 적용한다. 저장소 전체·환경 파일을
   모델 작업 공간에 복사하지 않는다.
4. SDK의 메시지 스트림에서 도구명, 대상 파일, 실행 상태, 최종 개선 제안,
   사용량을 기존 request ID의 이벤트로 기록한다. 비공개 사고 과정은 표시하지 않는다.
5. 대시보드에서 Claude 연결 상태와 제안을 표시한다. 사용자의 결정은 별도 이력으로
   저장한다. 현재 허용 범위에서는 코드 적용·커밋·파서 재실행을 하지 않는다.

위 3~5의 어댑터는 후속 구현 사항이다. 이번 변경에서 Claude 호출은 0회다.
단순 문서 판정만 맡기려면 Claude API를 LangGraph 노드에서 호출해도 된다.
파일/도구 사용 과정을 관측하려는 목적에는 Claude Agent SDK가 더 직접적이다.

공식 문서:
- [Agent SDK 개요](https://code.claude.com/docs/en/agent-sdk/overview)
- [설치·API 인증](https://code.claude.com/docs/en/agent-sdk/quickstart)
- [도구 권한 제어](https://code.claude.com/docs/en/agent-sdk/permissions)
