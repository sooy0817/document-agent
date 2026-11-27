# 표 구조 계약과 후보 생성

현재 범위는 [DESIGN.md](DESIGN.md), 코드 배치는 [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md),
물리 변경 제한은 [PARSER_FREEZE.md](PARSER_FREEZE.md)를 따른다.
이 문서는 구현된 계약을 설명하며 모델 필드·기본값·알고리즘을 변경하지 않는다.
초기 설계 배경은 [ADR-003](ADR/003-table-structure-layer.md),
기존 파서가 없었던 시작 조건은 [ADR-004](ADR/004-bootstrap-ir-contract.md)에 있다.

## 입력과 출력

| 계층 | 실제 계약 파일 | 역할 |
|---|---|---|
| DocumentIR | [domain/models.py](../src/doc_understanding/domain/models.py) | 원문 span·표 후보·좌표·출처 |
| PhysicalTableIR 2.1 | [physical/physical_models.py](../src/doc_understanding/parser/physical/physical_models.py) | 물리 격자·셀·경계·병합·미해결 근거 |
| StructuredTableIR 2.0 | [structure/models.py](../src/doc_understanding/parser/structure/models.py) | 역할·패널·행/열 트리·값 셀 경로 후보 |
| CanonicalDocument | [domain/pipeline_contracts.py](../src/doc_understanding/domain/pipeline_contracts.py) | 문서 단위 원본·후보·본문·표 및 논리 연결 |

루트 `parser/contracts.py`에는 공용 프로토콜과 초기 초안이 있다.
실제 의미 결과를 읽을 때는 `structure/models.py` 및
[structured-table-ir.schema.json](../schemas/structured-table-ir.schema.json)을 따른다.
과거 1.0 JSON 예시나 제안 상태 목록을 현재 2.0 계약에 그대로 적용하지 않는다.

## 물리 표 계약

PhysicalTableIR은 `source_ir_sha256`, 실행·문서·표 ID, 페이지·영역, 행·열 격자,
`cells`, 원본 `source_cells`와 `source_links`, 경계·미해결 경계, 출처 및 진단을 가진다.
PhysicalCell은 원문 `original_text`, `source_bbox`, `source`, `span_refs`, 점유 슬롯,
행·열 위치와 병합 span을 보존한다. 모르는 위치·span은 허용된 null/미해결 상태로 남긴다.

| 상태 필드 | 허용값 | 해석 |
|---|---|---|
| PhysicalCell.status | resolved / inferred / unresolved | 물리 복원 상태 |
| Boundary.status | present / absent / uncertain / inferred | 경계 근거 상태 |
| PhysicalTableIR.analysis_state | candidate / validated / partial / review_required / invalid | 기하·구조 검사 상태 |

물리 `validated`는 행·열 의미나 값의 업무상 정답을 승인하지 않는다.
원본 셀의 공란·0·부호와 원문 문자열을 보존하며, 합계에 맞춘 수치 교정을 하지 않는다.
임계값은 `GridPolicy`와 실제 구현을 기준으로 확인한다. 문서 정리를 이유로 변경하지 않는다.

## 의미 구조 계약

StructuredTableIR은 물리 입력의 해시·버전·경계를 참조하고 다음 후보를 가진다.

| 필드/모델 | 내용 |
|---|---|
| panels / Panel | 독립 열 영역, header_rows·stub_columns 후보 |
| features / StructuralFeature | 구조 특성·근거 셀·규칙·관찰/후보/불명 상태 |
| row_tree, column_tree / HierarchyNode | 축·패널·부모 후보·원본 셀·원문·좌표·점유 범위 |
| cell_roles / CellRole | corner / row_label / column_header / value / unknown |
| cell_mappings / CellPath | 값 셀의 원문·좌표와 row_path·column_path, 대안 후보 |
| ambiguities, issues | 대상·후보 ID, 검토 영역, 사유와 검증 진단 |

`row_path`와 `column_path`는 트리 노드 ID의 경로다. 라벨 문자열은 해당 노드에서
조회한다. 같은 글자가 반복돼도 부모·패널·원본 ID를 함께 확인한다.
`cell_mappings`는 값 역할 셀의 매핑이며 모든 라벨 셀의 경로 목록은 아니다.
라벨 자신의 위치는 해당 축 트리를 조회해야 한다.

의미 `analysis_state`는 `candidate | partial | review_required | invalid`다.
현재 계약에 `validated`는 없으며 `independent_human_review`는 `pending`이다.
근거 상태 타입은 `observed | inferred | unresolved`를 구분하지만 의미 계층을
물리 관찰만으로 observed/정답으로 승격하지 않는다.

## 기본 후보 생성 순서

구현: [structure/analyzer.py](../src/doc_understanding/parser/structure/analyzer.py)의
`SemanticTableAnalyzer.analyze()`.

1. 셀 점유·좌상단 셀·본문 선행 병합을 이용해 헤더와 행 라벨 경계 후보를 만든다.
   명시적 원본 배치 힌트가 있으면 범위를 검사한다. 단일 경계로 설명하지 못하는 셀은 보류한다.
   한 줄 머리글·한 열 라벨의 병합 없는 표는, 첫 행이 서로 다른 글자 라벨이고 본문 3행 이상에서
   숫자·날짜 열이 둘 이상(값 열의 절반 이상) 반복되면 글자 종류 대비를 근거로 삼는다.
   머리글과 대비에 사용한 값의 원문 span·좌표를 확인하고 `SOURCE_HEADER_TYPE_CONTRAST`에 기록한다.
   `2024년(A)`처럼 연도가 명시된 머리글은 인접한 둘 이상의 열에서 1년씩 증가하거나 감소할 때 검사한다.
   이때 모든 행 라벨에 문자가 있고 모든 값 열에 숫자가 세 개 이상 있으며 비어 있지 않은 값이
   모두 숫자 형식이어야 한다. 행 라벨의 원문 span과 좌표까지 확인해 `source-header/consecutive-years-1`로 기록한다.
   빈 머리글·숫자인 첫 행·짧은 표·불완전 격자·원문 누락은 이 근거로 승격하지 않는다.
2. 반복 패널 근거를 검사하고 독립 패널별로 셀 역할을 나눈다.
   머리글 1행·행 라벨 1열을 개별 인자나 행별 배치로 지정해도 같은 반복 패널 검사를 적용한다.
   기간 상한이 좌우에 반복되는 표를 조회할 때는 물리 격자의 같은 패널에서 구간 라벨·값 머리글·값 칸을 대조한다.
   구간이 겹치거나 월과 일의 경계를 확정할 수 없으면 보류한다. 기간을 담은 값이 질문에 명시된 항목과 다른 열에 속해도 보류한다.
3. `trees.py`에서 부모 후보와 행·열 트리를 구성한다. 근거가 부족한 헤더·라벨은
   모호성을 남기고, 반복 문구를 이름만으로 합치지 않는다.
4. 값 역할 셀을 원본 점유와 축 leaf에 연결하고 경로 또는 대안 후보를 기록한다.
5. `validation.py`에서 원본·기하·역할·트리·경로 일관성을 검사한다.
   검사를 통과해도 독립 사람 검증 전 후보 상태를 유지한다.

기본 analyzer 자체는 모델을 호출하지 않는다. 문서 조립의 번호·논리 연결 후보와
별도 모델 검토를 기본 기하 분석과 혼동하지 않는다.

## 표 사이 관계와 문맥

- `relations/logical_tables.py`: 분리된 헤더와 인접 페이지 표 조각의 근거 기반 연결 후보.
  CanonicalDocument의 `logical_table_links`는 원본 표·셀 ID와 후보 경로를 기록하며
  원본 물리 셀이나 저장된 StructuredTableIR을 덮어쓰지 않는다.
- `relations/header_inheritance.py`: 열 경계·쪽 끝/시작·중간 본문 부재·연번 연속·열별 내용 유형이
  모두 일치하고 앞쪽 머리글도 뒷받침될 때만 연결을 `accepted`로 채택한다. 현재 자동 채택은
  한 줄 머리글·한 열 연번·병합 없는 격자에 한정한다. 같은 열 수만으로는 채택하지 않는다.
  근거 부족·다른 연번·경쟁 앞 표는 사유와 함께 `candidate`로 남긴다.
  앞쪽 같은 열 너비의 표 중 실제 쪽 끝에 닿는 후보가 있으면 쪽 중간의 표는 경쟁자로 세지 않는다.
  뒤 조각이 두 행이면 앞 조각 3행 이상의 연번이 두 행 모두 이어지고, 빈칸 없이 서로 다른
  텍스트형·숫자형 열도 각각 일치해야 한다. 한 행은 이 규칙으로 채택하지 않는다.
  대상 조각의 첫 데이터 행은 머리글 없는 구조로 다시 분석한다. 값마다 빌린 머리글의 원본 셀 ID를
  연결의 `paths`에 기록하고, 읽기 투영에서만 원본 열 노드를 참조한다. 물리 조각을 합치거나
  머리글 글자를 새 셀로 만들지 않는다. 원래 대상 매핑은 연결 없이 사용되지 않도록 미확정으로 보존한다.
  `accepted`는 규칙의 연결 채택이며 사람의 의미 검증이 아니다. 후보 연결은 답변 근거를 승격하지 않는다.
- `relations/continuation_graph.py`: 선택적으로 실행하는 계속표 검토.
- `context/period_*`: 기간 설명·정의·적용 범위의 별도 후보 실행.
- `parser/assembly/nested_content.py`: 중첩 표와 부모 셀의 본문·표·주석 연결 근거.

새 섹션, 다른 열 구조, 다른 단위·캡션, 경쟁하는 부모 등 반례를 확인한다.
연결 근거가 없으면 강제로 합치지 않으며, 설명·주석은 값 경로와 구분한다.

## 모델 검토와 원본 보존

선택적 의미 보완은 `agents/semantic_resolver/`, `agents/axis_review/` 및 검토 팀에서
각 실행 정책에 따라 수행한다. `VisionStructureRequest.max_calls=0`인 기본 계약은
실행 가능한 모델 호출을 뜻하지 않는다. 별도 검토 경로의 호출·예산과 구분한다.

- 입력은 허용된 원본 ID·영역·근거와 구조 후보로 제한한다.
- 출력은 신뢰하지 않은 후보이며 schema·ID 소속·기하·연결을 검증한다.
- 존재하지 않는 셀·숫자·좌표·근거를 만들거나 기존 IR에 덮어쓰지 않는다.
- 모호성·실패·타임아웃은 보존한다. 무제한 재시도하지 않는다.
- 모델 결과·Judge 일치·검토 큐 완료는 사람 승인이나 금융 Fact 발행 조건이 아니다.

## 검증과 회귀

[structure/validation.py](../src/doc_understanding/parser/structure/validation.py)는
원본 ID·텍스트·좌표·점유, 부모·패널, 순환, 역할 커버리지, 값 매핑과 경로를 검사한다.
`SOURCE_TEXT_CHANGED`, `SOURCE_GEOMETRY_MISMATCH`, `UNCERTAINTY_LOST`, `TREE_CYCLE`,
`MAPPING_COVERAGE`, `INVALID_PATH`, `PANEL_CROSS_MAPPING` 등 실제 진단을 사용한다.
`NO_BODY_MAPPING`처럼 기하 통과만으로 완전성을 주장하지 못하게 하는 진단도 있다.

구조 검증과 숫자 산식·업무 의미 검증은 별개다. 출처가 맞아도 관계 해석이 틀릴 수 있다.
테스트는 다단 헤더·계층 행·병렬 패널·중첩 표·계속표·서술/주석·비표 음성 사례를 포함하고,
개발·회귀·holdout을 구분한다. 기관별 사례는 테스트 근거이며 런타임 하드코딩 근거가 아니다.

물리 동결 검사와 기존 의미 계약 검사는 [개발 안내](DEVELOPMENT.md)의 명령을 따른다.
구조 로직 변경은 승인되었지만 이번 작업에서 물리 알고리즘·격자·원문·KB 기준은 변경하지 않는다.

## 저장 없이 구조 문서 다시 만들기

문서 조립과 저장본 재구성은 다음 쪽의 첫 내용이 `2. 거래조건(계속)`과 같은 번호·제목이고,
쪽 상단에 있으며 직전 쪽 마지막 내용이 같은 번호·제목·부모의 섹션에 속할 때만 한 섹션으로 잇는다.
각 인쇄 제목 줄과 원문 참조를 보존한다. 제목이 없거나 표가 첫 제목보다 앞에 있으면 소속을 채우지 않는다.
소속이 비어 있는 영역에서는 같은 쪽에 같은 기호의 짧은 제목 두 개 이상이 각각 표 바로 위에 있을 때
제목과 바로 아래 표를 연결한다. 조문 제목의 명사를 바로 아래 문단이 주어로 되받는 경우에는
제목과 그 문단만 연결한다. 개정 연월이 붙은 양식번호는 섹션을 시작하지 않는다.

공개 출력의 복원 검사는 `scripts/audit_tabledoc_readback.py`를 사용한다. 각 입력의 `group`,
`label`, CanonicalDocument 파일 `path`, 선택적 실제 출력 `public_path`를 JSON 목록으로 전달한다.
검사는 공개 JSON만 읽어 구성한 결과를 내부 참조와 대조한다. 스키마 위반·관계 누락은 종료 코드 1이다.
`scripts/audit_section_ownership.py <manifest> <sample> <out>`는 파일 크기 순으로 고른 표본을
원래 목록과 대조하고, 소속 없는 표가 첫 제목 앞에 있는지 또는 섹션 없는 문서에 있는지 센다.

`scripts/rebuild_table_structure.py`는 기존 manifest의 문서를 한 건씩 읽어, 변경 대상인 모호한
머리글을 원본 PhysicalTableIR에서 다시 분석하고 조각 연결을 새로 만든다. 저장된 과거
모델/규칙 배치 결정과 무관한 경로는 보존한다. DocumentIR·물리 표의 해시 일치와 문서 참조
검증을 통과한 새 CanonicalDocument와 manifest를 별도 폴더에 쓴다. 기존 출력 폴더는 거절한다.
DB·색인·OCR·모델 호출은 하지 않는다. 과거 구조 JSON을 단순 복사하는 실행과 구분한다.

```powershell
.venv/Scripts/python.exe -X utf8 scripts/rebuild_table_structure.py --manifest work/reload-1123/manifest.json --out work/table-structure-new
.venv/Scripts/python.exe -X utf8 scripts/audit_table_usability.py --manifest work/table-structure-new/manifest.json --out work/table-structure-new/usability.json
```

정답표는 `scripts/evaluate_mapping.py --set config/evaluation/mapping-dev-v1.json --candidate <해당 문서 JSON들>`과
holdout 세트로 각각 검사한다. 새 manifest의 `document_path`는 이후 별도 DB에 적재할 문서 입력이다.
이 명령은 적재까지 실행하지 않으며 원래 5433·5546 DB와 기존 평가 기록은 유지한다.

사용 가능 집계는 빈 경로 라벨을 원본 셀·원문 span과 대조한다. 원본 추출상 공란,
글자가 남은 큰 원본 셀의 분할 후보, 빈 영역 안의 원문 글자, 판독 불가, 출처 부족을 구분한다.
글자가 있는 원본 셀에 연결됐어도 빈 영역의 글자나 분할 근거가 없으면 원인 미확정으로 남긴다.
분할은 후보일 뿐이며 원래 공란이라는 의미 판단이나 라벨 채우기를 하지 않는다.
첫 번째 보류 사유의 값 수와 모든 보류 사유의 값 수·라벨 노드 수를 따로 기록한다.
행·열 경로 누락은 패널 모호성, 축·라벨 부재, 범위를 덮는 라벨 부재, 복수 후보,
여러 머리글에 걸친 값 칸으로 나눠 기록한다. 충돌은 같은 노드 경로와 같은 라벨 글자를
구분하며 같은 행·다른 행을 표시한다. 이 진단은 경로나 사용 가능 판정을 바꾸지 않는다.

### 상위 행 제목의 모델 비교 평가

`scripts/evaluate_row_groups.py`는 선택한 값 셀의 **직접 행 이름을 제외한 상위 행 제목**을 바깥쪽부터 셀 ID로 비교한다. 모델에는 표 전체의 셀 원문·좌표·병합과 대상 ID를 보낸다. 파서 경로와 사람 정답은 모델 요청에서 제외한다. 현재 실험은 텍스트·좌표 입력을 사용한다. 사람이 검토할 때는 원본 PDF도 연다.

```powershell
$py = ".venv/Scripts/python.exe"
$bundle = "work/row-group-review-1124"
& $py scripts/evaluate_row_groups.py prepare --manifest work/path-coverage-1124/candidate/manifest.json --selection config/evaluation/row-group-pilot.json --out $bundle
& $py scripts/evaluate_row_groups.py predict --inputs "$bundle/inputs.json" --out "$bundle/offline"
& $py scripts/evaluate_row_groups.py score --bundle $bundle --reference "$bundle/reference.json" --predictions "$bundle/offline/predictions.json" --out "$bundle/offline-report.json"
```

1. `review.html`을 열어 원본의 쪽과 셀 ID를 확인하고 각 대상의 상위 제목 있음·없음·판단 보류를 표시한다. 검토 JSON을 내려받는다. 미검토는 채점에서 제외한다.
2. 실제 모델을 호출할 때는 위 `predict`에 `--allow-external --policy <CallPolicy JSON 경로> --max-total-usd 0.5`를 붙이고 새 출력 폴더를 지정한다. 기존 OpenAI Responses 호출기를 사용하며 `OPENAI_API_KEY`와 실제 모델명·단가를 설정한다. 정책 파일에는 `model`, `input_usd_per_million`, `output_usd_per_million`을 넣는다. 필요하면 `max_output_tokens`, `max_cost_usd`를 설정한다. 외부 호출 시 표 전체의 셀 텍스트와 좌표가 전송된다.
3. `score`에 내려받은 검토 파일과 실제 실행의 `predictions.json`을 지정한다. 새 결과 파일에서 dev와 holdout별 복원·오변경·없는 제목 추가·기권·호출 실패, 해당 표 수와 셀별 세 결과를 확인한다. 한 표에서 여러 결과가 나올 수 있다. `recovered`에는 잘못 붙인 제목을 올바르게 없앤 경우도 포함한다. 출력 파일과 실행 디렉터리는 새 경로를 사용한다.
4. 표본을 추가할 때는 selection에 문서 ID, 표 위치, 0부터 세는 대상 행·열, 양식 family와 split을 기록한다. 같은 양식·문서는 한 split에 둔다. 파일럿은 소수의 선택 셀을 대상으로 하며 전체 파서 정확도를 대표하지 않는다.

검토 화면과 모델 입력은 파서가 부모 셀 하나로 연결한 안쪽 표를 해당 칸에 배치한다. 원문 전체와 읽기 순서가 확인되면 평탄화된 중복 텍스트를 단위 문장과 안쪽 격자로 표시한다. 안쪽 표를 담는 칸은 채점 대상에서 제외하고, 안쪽 값은 해당 표의 행·열 위치로 선택한다.

## HTML 표 입력

Upstage Document Parse 응답의 `content.html` 문자열을 UTF-8 HTML 파일로 저장한 뒤
`python -m doc_understanding.parser.extraction.html_adapter --html input.html --output runs`로 읽는다.
응답에 포함된 바깥 표와 안쪽 표는 각각 물리 표 ID를 받는다. 안쪽 표의
`source_references[1].cell_id`는 이를 담은 바깥 물리 셀 ID다.
중첩 표가 있는 실행에서는 `html-table-links.json`에 부모 표·부모 셀·자식 표 ID를 저장한다.
셀의 `rowspan`, `colspan`, `occupied_slots`는 병합 범위를 나타낸다. 본문의 첫 열부터
연속한 `th` 범위가 행마다 일치하면 그 너비를 행 제목 열 수로 사용한다.

HTML 입력의 페이지 번호와 좌표는 합성 배치 기준이다. 바깥 셀의 텍스트와 자식 표의
텍스트는 따로 저장하며, 한 셀에 본문과 표가 섞인 경우 상대적인 읽기 순서는 미확정으로 둔다.
HTML에 단일 격자로 표현된 표는 해당 격자로 유지한다.
