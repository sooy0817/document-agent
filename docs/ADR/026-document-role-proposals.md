# ADR-026 — 원본 줄 기반 문서 역할 후보 Sidecar

상태: 제한된 개발 표본 구현. 의미 정확도 및 범용성 검증 미완료.

기존 DocumentIR/Layout/PhysicalTableIR/StructuredTableIR은 변경하지 않는다.
`staging.role_proposals`는 기존 bundle을 읽어 별도 역할 후보를 생성한다.
한 원본 블록의 제목과 단위를 span의 line_index로 분리하여 원본 block/span ID,
좌표와 텍스트를 유지한다. 원본 블록을 재작성하지 않는다.

역할은 명시 표기(단위/주석), 상대 글자 크기, 표 영역 후보로 제안한다.
페이지 번호·기관명·표 제목별 정답 규칙은 없다. 본문 기본값은 unresolved다.
가까운 수직 방향과 수평 겹침으로 제목/단위/주석의 표 연결 후보를 만든다.
여러 표가 가능하면 모두 남기며 unresolved다. 유일한 표도 inferred일 뿐 승인되지 않는다.
행·열별 단위/주석 적용 범위는 항상 unresolved다. 다단 장절 및 페이지 간 연결은 미구현이다.
표의 StructuredTableIR 참조도 같은 페이지의 영역 겹침 후보이며 동일성 확정은 아니다.

검토 JSON의 맞음/수정/보류와 검토자 필드는 사람 입력용이다. 자동 승인 이벤트를 만들지 않는다.
검토 파일을 다시 생성하여 덮어쓰지 않는다. Gold 확정 및 운영 발행과 분리한다.
원본, 전체 proposal, 축약 검토 JSON을 별도로 보존한다.

실행:
```
.venv/Scripts/python.exe -m doc_understanding.staging.role_proposals --bundle data/document-json-review/current-full.json --pages 2 3 --output data/document-json-review/role-candidates-v1
```

외부 API/DB/HTML/공개 패키지 변경 없음. 이 단계는 오프라인 후보 분류이며 Agent 의미 이해의 정확도를 입증하지 않는다.
