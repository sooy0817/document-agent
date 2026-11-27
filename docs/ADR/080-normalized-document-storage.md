# ADR-080: 문서 저장 구조 — 보관 묶음과 정규화된 서빙 표

- 상태: 채택(설계). 구현 전이며 단계마다 재적재와 평가로 확인한다.
- 개정: 2026-11-23 구조·답변 코드와 대조한 검토를 두 차례 반영해 3~6절과 옮기는 순서를 고쳤다.
- 날짜: 2026-11-23

## 배경

지금은 분석 결과 JSON을 DB에 통째로 넣고, 필요한 것을 다시 뽑아 따로 넣는다. 현재 검색 대상 112건(1,060쪽)을
복제한 DB에서 잰 값이다(JSON 원문 기준).

| 저장 위치 | 크기 | 질문할 때 읽는가 |
|---|---:|---|
| `staging.artifact` CanonicalDocument | 1,228MB | 일부 경로에서 문서 전체를 읽는다 |
| `staging.artifact` DocumentIR·PhysicalTableIR·StructuredTableIR | 1,048MB | 아니오. CanonicalDocument 안에 이미 들어 있는 것을 다시 저장한다 |
| `staging.artifact` DocumentLayoutProjection | 260MB | 아니오 |
| `staging.evidence` | 647MB(도형 374,258개가 321MB) | 읽는 코드가 없다(src·scripts·tests) |
| `staging.content_block` | 242MB | 검증 스크립트만 |
| `rag.evidence_item` | 261MB | 예 |
| `rag.search_entry` | 72MB(검색 문자열은 10.8MB) | 예 |
| `rag.entry_embedding` | 309MB | 예 |

문서 한 건에 약 36MB이고, 같은 비율이면 2만 건에 700GB쯤이다. 구조 문제는 다음과 같다.

- 같은 내용이 두 벌 이상 들어간다. 도형은 세 곳에 있다. 무거운 문서 한 건을 적재하면 335MB짜리 SQL 한 문장이 되고
  프로세스가 4~5GB를 쓴다.
- 값 칸마다 행·열 경로 배열과 표 전체의 주석을 복사한다. 머리글 하나를 고치려면 그 아래 모든 칸을 다시 써야 한다.
- 식별자마다 64자 문서 해시가 들어가고 한 칸의 기록 안에서 여러 번 반복된다.
- 라벨이 원문 한 가지뿐이다. 한 글자씩 줄이 바뀐 세로쓰기 라벨(`자\n산`)이 검색 문자열에 그대로 들어간다
  (검색 항목 33,890개 중 1,288개, 문서 45건).
- 무엇을 고치든 전부 다시 적재한다. 적재 실행 157개 중 현재 쓰는 것은 112개이고 나머지를 지우는 절차가 없다.

## 결정

### 1. 용도에 따라 세 층으로 나눈다

| 층 | 무엇 | 형태 |
|---|---|---|
| A. 보관 | 원본, 분석 기록(글자·도형·좌표), 표 격자, 구조 검토 판정 | 문서당 묶음 하나. 한 번 쓰면 바꾸지 않는다 |
| B. 서빙 | 쪽, 표, 머리글 트리, 칸, 본문, 문맥 연결 | 정규화된 표. 질문할 때 읽는다 |
| C. 색인 | 검색 문자열, 벡터 | B에서 다시 만들 수 있다 |

큰 JSON은 A에만 둔다. B와 C에는 도형을 넣지 않는다(읽는 곳은 분석과 구조 검토의 배치 판정뿐이다).
묶음을 폴더에 둘지 DB의 별도 표에 압축해 둘지는 설치할 때 고른다. 코드는 "묶음을 쓴다·읽는다"만 안다.

### 2. 보관 묶음

```
<원본 sha256>/
  manifest.json      파일 목록, 각 해시, 분석·구조 버전
  source.pdf         원본
  pages.jsonl        쪽마다 한 줄: 글자와 좌표
  drawings.jsonl     쪽마다 한 줄: 도형. 표 경계를 찾을 때만 읽는다
  tables.jsonl       물리 표마다 한 줄: 칸 격자와 병합(PhysicalTableIR, 동결된 형식 그대로)
  review.json        구조 검토 판정(규칙·모델)과 근거
```

묶음은 이동 단위이기도 하다. 분석한 장비와 적재하는 장비가 달라도 되고, DB는 묶음에서 다시 만들 수 있다.

### 3. 서빙 표

```
document        document_id(짧은 번호), sha256(여기에만), source_name, display_name, document_label, media_type,
                source_uri
document_run    run_id, document_id, is_current, analysis_version, structure_version, index_version,
                bundle_location, created_at
page            run_id, page_no, width, height, text_status
logical_table   logical_table_id, run_id, title, state, row_axis, column_axis
table_block     table_id, logical_table_id, fragment_no, page_no, bbox, state, layout_review, source_table_ref
table_panel     table_id, panel_no, header_rows, label_columns, first_col, last_col
table_link      from_table_id, to_table_id, kind, state, reasons
grid_cell       cell_id, table_id, row_no, col_no, rowspan, colspan, role, text_raw, value_kind, bbox,
                source_cell_ref, panel_no
axis_node       node_id, logical_table_id, axis(row|column), panel_no, parent_id, position,
                label_raw, label_text, state
axis_node_occurrence   node_id, cell_id
cell_path       cell_id, row_node_id, col_node_id, path_state, lookup_eligible, withhold_reason
cell_path_source   cell_id, axis, source_cell_id, via_link(table_link), role(adopted|candidate), reasons
cell_group      group_id, table_id, kind  /  cell_group_member  group_id, cell_id, position
cell_container  parent_cell_id, containment_state, coverage_state, reading_order_state, duplicate_of_children
container_item  parent_cell_id, position, item_kind(table|content|native_table), item_id, source_table_ref
container_label parent_cell_id, position, label_cell_id
content         content_id, run_id, page_no, position, kind, text_raw, bbox, body
context_link    from_kind, from_id, to_content_id, relation, scope, state, decision_source(rule|model)
source_ref      owner_kind, owner_id, position, page_no, bbox, block_ref, span_ref, cell_ref,
                char_start, char_end
search_entry    entry_id, run_id, kind, search_text, primary_start, primary_end, join_separator,
                projection_version
search_entry_source   entry_id, position, role(hit|row_neighbor|context), ref_kind, ref_id,
                      source_start, source_end, entry_start, entry_end
entry_embedding entry_id, profile_id, input_sha256, vector
```

- **물리 격자를 그대로 둔다.** `grid_cell`은 값 칸만이 아니라 머리글·라벨·값·빈칸을 모두 담고 병합 범위(`rowspan`,
  `colspan`)와 역할을 가진다. 답변 시점의 물리 격자 대조(구간 라벨·열 머리글·값 칸이 함께 맞는지)와 원문 화면이
  이 표를 읽는다. 지금 `staging.artifact`의 문서 전체에서 읽던 것을 대신한다.
- **채택된 경로와 그 근거를 나눈다.** `cell_path`는 채택된 행·열 잎 노드 한 쌍이고, 경로는 `parent_id`를 따라
  올라가며 읽는다. 칸마다 경로 배열을 복사하지 않는다. 어느 원본 칸에서 그 경로를 얻었는지와 경쟁 후보는
  `cell_path_source`에 둔다.
- **보류는 이유와 함께 둔다.** `lookup_eligible` 하나로는 부족하다. 이웃 내용과 중복된 부모 칸, 라벨 경로 충돌,
  머리글 근거 부족은 읽는 쪽에서 다르게 다룬다(표 전체를 읽을 때 중복 부모 칸은 미해결 칸 수에서 뺀다).
  `withhold_reason`에 구분해서 적는다.
- **축이 필요 없는 표와 복원하지 못한 표를 구분한다.** `logical_table.row_axis`·`column_axis`는
  `present`·`not_applicable`·`unrecovered` 중 하나다. 머리글이 원래 없는 표는 행 경로만으로 조회할 수 있어야 하고,
  머리글이 있어야 하는데 복원하지 못한 표만 막는다.
- **여러 칸이 한 값을 이루는 경우**(같은 행의 `해당 여부 | 설명`처럼 함께 인용하는 칸)는 `cell_group`으로 묶는다.
  이웃 칸의 원문과 출처는 `grid_cell`에서 읽는다.
- **라벨은 노드에 한 번, 두 가지로 둔다.** `label_raw`는 원문 그대로, `label_text`는 검색·표시용이다.
- **출처는 순서 있는 참조 목록이다.** 칸·본문·인용은 참조를 여러 개 가질 수 있고, 인용에는 첫 참조와 전체 참조가
  따로 쓰인다. 참조마다 쪽·좌표가 있고 블록·글자 조각·칸 번호는 있을 수도 없을 수도 있다. `source_ref`에
  `position` 순서로 둔다. 한 물리 칸이 원본 추출 칸 여러 개에 대응하면 `cell_ref`가 든 줄이 여러 개다
  (`grid_cell.source_cell_ref`는 대표 하나이고 전체는 `source_ref`에서 읽는다).
- **검색 항목은 여러 원문을 가리킬 수 있다.** 여러 본문을 합친 조각, 긴 칸의 일부, 같은 행의 이웃 칸이 있다.
  `search_entry_source`의 `role`로 직접 적중한 원문(`hit`), 같은 행의 이웃 칸(`row_neighbor`), 문맥(`context`)을
  구분하고, 원문에서의 범위와 합친 문자열에서의 범위를 따로 둔다. `projection_version`으로 검색 문자열을 원문과
  다시 대조한다.
- **키워드 점수에 쓰는 본문 부분**(지금의 `primary_text`)은 문자열을 한 번 더 저장하지 않고 `search_text` 안의
  범위(`primary_start`, `primary_end`)로 둔다.
- **문서 이름은 세 가지다.** 원본 파일명(`source_name`), 사용자가 붙인 표시 이름(`display_name`), 첫 쪽 제목 후보를
  붙인 검색·재순위용 이름표(`document_label`)는 서로 다른 값이다. 문서 선택이 읽는 메타데이터(기관·제목·종류·
  기준 시점·별칭)는 지금의 `rag.document_metadata`를 그대로 쓴다. 문서 단위라 재적재해도 유지된다.
- **표의 머리글 영역은 패널별 머리글 행 수로 정한다.** 표의 문맥 문자열(지금의 `table_context`)은 `table_panel`의
  `header_rows` 범위에 있는 `grid_cell`을 격자 순서로 모아 만든다. 칸의 역할만으로 같은 목록과 순서가 나온다고
  가정하지 않는다. 배치 검토가 적용됐는지(`layout_review`)와 범위 판정의 출처(`decision_source`)는 다른 값이다.
- **벡터는 실행·검색 문구·모델에 묶인다.** `entry_id`는 실행 안의 검색 항목이고, `input_sha256`은 벡터를 만든
  `search_text`의 해시다. 조회할 때 현재 `search_text`의 해시와 프로필(모델·차원·입력 형식 버전)이 맞는 벡터만 쓴다.
  색인 버전은 `document_run.index_version`에서 대조한다.
- 주석·단위·적용 범위는 `context_link` 한 줄로 표현한다. 칸마다 복사하지 않는다.

### 4. 칸 안에 표가 들어 있는 경우

지금 코드는 자식 표에 부모 문맥을 붙이기 전에 세 가지를 모두 확인한다: 포함 관계가 성립하는가, 부모 칸의 글자
전체가 자식 항목으로 설명되는가, 읽기 순서가 정해졌는가. 부모 칸 안에 표 둘이 나란히 있으면 포함은 성립해도
읽기 순서는 부분적이다. 세 상태를 하나로 합치지 않는다.

- `cell_container`: 부모 칸 한 줄에 `containment_state`·`coverage_state`·`reading_order_state`를 따로 둔다.
  셋이 모두 충족될 때만 부모 문맥을 자식 값에 붙인다. 하나라도 아니면 자식 표는 독립된 표로만 쓰인다.
- `container_item`: 부모 칸 안의 자식 표와 글 조각을 `position` 순서로 적는다. 표와 글이 같은 순서 열을 쓴다.
  물리 표로 대응되지 않은 원본 추출 표도 항목으로 남는다(`native_table`, 원본 번호는 `source_table_ref`).
  부모가 원본 추출 표의 번호로만 식별되는 경우도 같은 열로 잇는다.
- `container_label`: 부모 문맥의 라벨은 부모 칸의 의미 트리 잎이 아니라, 같은 행 범위의 왼쪽에 **실제로 인쇄된 칸**에서
  얻는다. 그 칸들을 순서대로 적는다. 인용할 때 원문 위치로 돌아갈 수 있어야 하기 때문이다.
- `duplicate_of_children`: 부모 칸의 글이 자식 내용과 겹치는 경우의 표시다. 이런 부모 칸은 값으로 조회하지 않고,
  표 전체를 읽을 때 미해결 칸으로 세지 않는다.
- 부모 칸 밖의 관련 문장(지금의 `outer_prose`)은 `context_link`로 잇는다.
- 표가 여러 겹이면 같은 관계를 반복한다. 경쟁하는 부모 후보는 보관 묶음의 판정 기록에 남긴다.

검색 문자열을 만들 때 부모 라벨을 앞에 붙이는 것은 색인 층(C)의 일이고 서빙 표에는 복사하지 않는다.

### 5. 쪽을 넘어가는 표

물리 표 조각은 합치지 않는다(지금의 원칙). 지금 `LogicalTableLink`는 항상 후보 상태이고, 같은 조각 안에서도 값 칸마다
어느 앞쪽 머리글·행 라벨을 빌렸는지와 경쟁 후보·보류 이유가 다르다. 그래서 **조각을 잇는 것**과
**칸이 경로를 빌리는 것**을 나눠 적는다.

- 조각은 `table_block` 한 줄씩이고 `logical_table`로 묶는다. 조각이 하나뿐인 표도 `logical_table` 한 줄을 가진다.
- 조각 사이의 연결은 `table_link`에 종류(`page_continuation`·`detached_header`)와 상태·근거로 남긴다.
- 머리글 트리는 `logical_table`에 속한다. 2쪽 조각의 칸이 1쪽 머리글의 노드를 가리킬 수 있다.
  머리글이 쪽마다 반복 인쇄됐으면 노드는 하나이고 인쇄된 칸마다 `axis_node_occurrence` 한 줄을 둔다.
- 칸이 다른 조각의 머리글이나 행 라벨을 빌렸으면 `cell_path_source`에 **칸마다** 적는다: 빌린 원본 칸, 거친 연결,
  채택인지 후보인지, 이유. 경쟁 후보도 같은 표에 `candidate`로 남긴다.
- 빌린 경로의 `path_state`는 거친 연결의 상태를 넘지 못한다. 연결이 후보이면 경로는 추론이고 확정 근거로 쓰지 않는다.
- 답변 시점에 앞쪽에서 열 수가 같은 표를 머리글 후보로 찾는 지금의 동작(물리 격자 경로)은 `grid_cell`과
  `table_link`로 같은 결과를 내야 한다. 옮길 때 이 경로를 따로 검사한다.
- 머리글 축이 없는 조각을 일괄로 막지 않는다. `column_axis`가 `not_applicable`이면 행 경로만으로 조회하고,
  `unrecovered`일 때만 막는다.

### 6. 좌우로 나뉜 표(패널)

한 물리 표 안에 같은 머리글이 좌우로 반복되는 경우다.

- `grid_cell.panel_no`와 `axis_node.panel_no`를 둔다. 패널이 없는 표는 모두 0이다.
- 칸의 행 노드와 열 노드는 칸과 같은 `panel_no`여야 한다. 저장할 때 검사한다.
- **이 검사는 패널을 옳게 나눈 뒤의 잘못된 연결만 잡는다.** 표 전체를 한 패널로 잘못 본 경우에는 틀린 라벨과 값이
  같은 번호를 가지므로 걸리지 않는다. 65문항의 h-03·h-04가 이 경우였다. 그래서 두 가지를 함께 유지한다.
  - 구조를 만들 때 반복 패널을 찾는다(머리글 행 수·라벨 열 수를 명시한 배치에서도).
  - 답변할 때 물리 격자에서 구간 라벨·값 열 머리글·값 칸이 함께 맞는지 대조하고, 경계가 모호하면 보류한다.
    이 대조가 `grid_cell`을 서빙 표에 두는 이유다.
- 같은 패널 안에서 열을 혼동하는 문제(가입기간과 실적 충족 개월)는 패널이 아니라 열 축의 문제다.
  열 노드의 경로와 질문의 요구를 대조하는 답변 쪽 검사로 다룬다.

### 7. 적재 단위와 버전

- 적재 단위는 문서 한 건, 트랜잭션 한 번이다. 묶음 쓰기 → 서빙 표 → 검색 항목 순서이고, 모두 끝나야 `is_current`가 바뀐다.
- 같은 문서를 다시 넣으면 새 `document_run`이 이전 실행을 한 번에 대체한다. 문서당 현재와 직전 실행만 남기고
  그 이전은 정리 명령으로 지운다.
- 버전을 넷으로 나눈다. 바뀐 것에 따라 다시 하는 범위가 정해진다.

| 바뀐 것 | 다시 하는 범위 |
|---|---|
| 임베딩 모델 | 벡터 |
| 검색 문자열 만드는 방식 | 검색 항목과 벡터(서빙 표에서 바로) |
| 구조 규칙·검토 판정 | 서빙 표와 색인(묶음의 표 격자에서) |
| 분석 | 원본부터 전부 |

## 예시

"운용 > 무수익성 > 비유동 > 투자자산 / 2024년(A) > 구성비 = 2.51"

```
axis_node   101  row     parent -    label_raw "운\n용"         label_text "운용"
            102  row     parent 101  label_raw "무\n수\n익\n성"  label_text "무수익성"
            103  row     parent 102  label_raw "비\n유\n동"      label_text "비유동"
            104  row     parent 103  label_raw "투자자산"        label_text "투자자산"
            201  column  parent -    label_raw "2024년(A)"
            202  column  parent 201  label_raw "구성비"
grid_cell   9182  table 31  row_no 23  col_no 2  role value  text_raw "2.51"
cell_path   9182  row_node_id 104  col_node_id 202  path_state inferred  lookup_eligible true
search_entry  kind 표의 행  ref 행 노드 104
              search_text "운용 > 무수익성 > 비유동 > 투자자산 / 2024년(A) 금액 4,612 · 구성비 2.51 …"
```

같은 행의 다른 칸도 `row_node_id = 104`를 가리킨다. "투자자산"이라는 글자는 한 번만 저장된다.

## 저장은 나누고, 보는 것은 합친다

표가 여럿으로 나뉘면 DB를 직접 열었을 때 번호만 보인다. 사람이 보는 길을 함께 둔다.

- **펼쳐 보기**: 쪽 하나·표 하나·문서 하나를 JSON 한 개로 모아 준다(읽는 순서의 항목, 표의 격자·머리글 트리·경로,
  본문과의 연결). 조사할 때 쓰고, 옮길 때는 "펼친 결과가 예전 저장본에서 뽑은 내용과 같은가"를 검사하는 데 쓴다.
- **이어 보는 조회**: `doc.v_cell_path`는 문서·쪽·표·행 경로·열 경로·값·상태를 한 줄로 보여 준다.
  운영자와 검증 담당자는 표를 직접 열지 않고 이 조회를 본다.
- **검토 화면**은 지금과 같은 모습이고 읽어 오는 곳만 바뀐다.

## 옮기는 순서

단계마다 별도 DB에 재적재하고 65문항 결과가 유지되는지 본다. 지금 진행 중인 P0 재적재·재평가를 먼저 끝낸다.

1. 읽지 않는 것의 적재 중단: `staging.evidence`, `staging.content_block`.
2. 문서 기록의 두 벌 저장 중단(CanonicalDocument와 그 안의 부분들).
3. 서빙 표를 만들고 읽는 쪽을 옮긴다. 대체 범위는 `rag.evidence_item`만이 아니다. 검색 조각의 원문 대조는
   `rag.search_entry.payload`에서, 물리 격자·칸 역할·앞쪽 표 모양은 `staging.artifact`에서 읽는다.
   세 가지를 따로 검사한다: (가) 표·본문 근거가 서빙 표에서 같은 내용으로 복원되는가, (나) 물리 격자 대조가
   같은 인용·보류 결과를 내는가, (다) 검색 조각이 같은 원문 범위로 돌아가는가.
4. 문서 기록을 DB에서 빼고 묶음으로 옮긴다.
5. 묶음 내보내기·들여오기와 지난 실행 정리 명령.

## 대안과 결과

- 지금처럼 JSON 통째 적재를 유지하고 압축만 한다: 크기는 줄지만 경로 복사·라벨·부분 갱신 문제가 남는다.
- 물리 표 조각을 논리 표 하나로 합쳐 저장한다: 읽기는 단순해지지만 원문 조각과 좌표의 대응이 사라지고,
  잘못 이은 연결을 되돌릴 수 없다. 조각은 그대로 두고 묶기만 한다.
- 경로를 칸마다 배열로 두고 중복만 줄인다: 저장량은 줄지만 머리글 하나를 고칠 때 모든 칸을 다시 쓰는 문제가 남는다.
- 서빙 표로 가면 칸 하나를 읽을 때 노드를 따라가는 조회가 생긴다. 표 하나를 통째로 읽는 지금의 사용 방식에서는
  표 단위로 노드와 칸을 한 번에 읽으면 되므로 영향이 작을 것으로 보지만, 3단계에서 응답 시간을 잰다.
- 저장 형식이 바뀌므로 색인 버전을 올리고 전부 한 번 다시 적재해야 한다. 3~4단계는 한 번에 묶어 버전을 한 번만 올린다.
- 크기 추정(측정 전): DB는 문서당 3~5MB로 대부분 벡터일 것으로 보았으나, 물리 격자와 출처 조각을 서빙 표에 두기로
  하면서 다시 재야 한다. 묶음의 압축 후 크기도 몇 건으로 재 본 뒤 적는다.

## 정하지 않은 것

- 사람이 고친 내용(검토·수정)을 어디에 둘지. 서빙 표를 직접 고치지 않고 수정 기록을 따로 두어 재적재 때 다시
  적용하는 쪽을 검토한다.
- 한 조각이 여러 논리 표에 걸칠 수 있는지(한 쪽에서 표가 끝나고 바로 다음 표가 시작하는 경우의 오검출).
- `axis_node.state`, `cell_path.path_state`, `withhold_reason`의 값 집합. 지금 코드의 매핑 상태와 보류 사유에 맞춘다.
- 경쟁 후보를 서빙 표(`cell_path_source`)에 어디까지 둘지. 답변과 검토 화면이 읽는 것만 두고 나머지는 보관 묶음에 둔다.
- 서빙 표가 늘어난 만큼의 크기. 물리 격자 전체를 두면 값 칸만 둘 때보다 커진다. 몇 건으로 재 본 뒤 추정을 고친다.

## 부록: 표 정의(초안)

PostgreSQL 16, 확장 `vector`(pgvector)를 전제로 한다. 이름과 형은 구현하면서 고칠 수 있다.
스키마는 셋이다: `doc`(서빙), `idx`(색인), `jobs`(작업 큐, 이미 있음).

```sql
-- 문서와 실행 ---------------------------------------------------------------
CREATE TABLE doc.document (
  document_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  sha256        char(64) NOT NULL UNIQUE,            -- 원본 내용 해시(여기에만)
  source_name   text NOT NULL,                        -- 원본 파일명
  display_name  text,                                 -- 사용자가 붙인 이름
  document_label text,                                -- 검색·재순위용 이름표
  media_type    text NOT NULL,
  source_uri    text,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE doc.document_run (
  run_id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  document_id       bigint NOT NULL REFERENCES doc.document,
  is_current        boolean NOT NULL DEFAULT false,
  status            text NOT NULL CHECK (status IN ('loading','ready','failed','retired')),
  analysis_version  text NOT NULL,
  structure_version text NOT NULL,
  index_version     text NOT NULL,
  bundle_location   text NOT NULL,                    -- 보관 묶음 위치
  bundle_sha256     char(64) NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX document_run_current ON doc.document_run (document_id) WHERE is_current;

-- 아래 표는 모두 run_id를 가진다. 실행을 지우면 ON DELETE CASCADE로 함께 지워진다. ----------
CREATE TABLE doc.page (
  run_id bigint NOT NULL REFERENCES doc.document_run ON DELETE CASCADE,
  page_no int NOT NULL, width real, height real,
  text_status text NOT NULL CHECK (text_status IN ('native','ocr_required','empty')),
  PRIMARY KEY (run_id, page_no)
);
CREATE TABLE doc.content (                             -- 본문·주석 한 줄이 한 행
  content_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id bigint NOT NULL REFERENCES doc.document_run ON DELETE CASCADE,
  page_no int NOT NULL, position int NOT NULL,         -- 쪽 안의 읽기 순서(표와 같은 번호 줄)
  kind text NOT NULL CHECK (kind IN ('paragraph','note','rule','image','unknown')),
  section_id bigint REFERENCES doc.content,           -- 속한 제목 줄
  is_section_title boolean NOT NULL DEFAULT false,
  text_raw text, body boolean NOT NULL DEFAULT true,   -- 표와 겹치는 글은 body=false
  x0 real, y0 real, x1 real, y1 real
);
CREATE INDEX content_order ON doc.content (run_id, page_no, position);

CREATE TABLE doc.logical_table (
  logical_table_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id bigint NOT NULL REFERENCES doc.document_run ON DELETE CASCADE,
  title text, state text NOT NULL,
  row_axis    text NOT NULL CHECK (row_axis    IN ('present','not_applicable','unrecovered')),
  column_axis text NOT NULL CHECK (column_axis IN ('present','not_applicable','unrecovered'))
);
CREATE TABLE doc.table_block (                         -- 물리 표 조각. 합치지 않는다
  table_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id bigint NOT NULL REFERENCES doc.document_run ON DELETE CASCADE,
  logical_table_id bigint NOT NULL REFERENCES doc.logical_table ON DELETE CASCADE,
  fragment_no int NOT NULL, page_no int NOT NULL, position int NOT NULL,
  state text NOT NULL CHECK (state IN ('candidate','validated','partial','review_required','invalid')),
  layout_review text, source_table_ref text NOT NULL,  -- 보관 묶음의 표 번호
  x0 real, y0 real, x1 real, y1 real,
  UNIQUE (logical_table_id, fragment_no)
);
CREATE TABLE doc.table_panel (
  table_id bigint NOT NULL REFERENCES doc.table_block ON DELETE CASCADE,
  panel_no int NOT NULL, header_rows int NOT NULL, label_columns int NOT NULL,
  first_col int NOT NULL, last_col int NOT NULL,
  PRIMARY KEY (table_id, panel_no)
);
CREATE TABLE doc.table_link (
  from_table_id bigint NOT NULL REFERENCES doc.table_block ON DELETE CASCADE,
  to_table_id   bigint NOT NULL REFERENCES doc.table_block ON DELETE CASCADE,
  kind  text NOT NULL CHECK (kind  IN ('page_continuation','detached_header')),
  state text NOT NULL CHECK (state IN ('candidate','confirmed','rejected')),
  reasons text[] NOT NULL DEFAULT '{}',
  PRIMARY KEY (from_table_id, to_table_id, kind)
);
CREATE TABLE doc.grid_cell (                           -- 원문 격자 전체(머리글·라벨·값·빈칸)
  cell_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  table_id bigint NOT NULL REFERENCES doc.table_block ON DELETE CASCADE,
  row_no int NOT NULL, col_no int NOT NULL, rowspan int NOT NULL DEFAULT 1, colspan int NOT NULL DEFAULT 1,
  panel_no int NOT NULL DEFAULT 0,
  role text NOT NULL CHECK (role IN ('corner','header','label','value','empty','unknown')),
  text_raw text, value_kind text,
  source_cell_ref text NOT NULL,
  x0 real, y0 real, x1 real, y1 real,
  UNIQUE (table_id, row_no, col_no)
);
CREATE TABLE doc.axis_node (                           -- 머리글 트리. 논리 표에 속한다
  node_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  logical_table_id bigint NOT NULL REFERENCES doc.logical_table ON DELETE CASCADE,
  axis text NOT NULL CHECK (axis IN ('row','column')),
  panel_no int NOT NULL DEFAULT 0,
  parent_id bigint REFERENCES doc.axis_node ON DELETE CASCADE,
  position int NOT NULL,
  label_raw text, label_text text,                     -- 원문 그대로 / 검색·표시용
  state text NOT NULL
);
CREATE INDEX axis_node_tree ON doc.axis_node (logical_table_id, axis, parent_id, position);
CREATE TABLE doc.axis_node_occurrence (                -- 노드가 인쇄된 칸(반복 머리글이면 여러 줄)
  node_id bigint NOT NULL REFERENCES doc.axis_node ON DELETE CASCADE,
  cell_id bigint NOT NULL REFERENCES doc.grid_cell ON DELETE CASCADE,
  PRIMARY KEY (node_id, cell_id)
);
CREATE TABLE doc.cell_path (                           -- 값 칸의 채택된 경로
  cell_id bigint PRIMARY KEY REFERENCES doc.grid_cell ON DELETE CASCADE,
  row_node_id bigint REFERENCES doc.axis_node, col_node_id bigint REFERENCES doc.axis_node,
  path_state text NOT NULL CHECK (path_state IN ('observed','inferred','unresolved')),
  lookup_eligible boolean NOT NULL,
  withhold_reason text
);
CREATE INDEX cell_path_row ON doc.cell_path (row_node_id);
CREATE INDEX cell_path_col ON doc.cell_path (col_node_id);
CREATE TABLE doc.cell_path_source (                    -- 경로를 얻은 원본 칸과 경쟁 후보
  cell_id bigint NOT NULL REFERENCES doc.grid_cell ON DELETE CASCADE,
  axis text NOT NULL, source_cell_id bigint NOT NULL REFERENCES doc.grid_cell ON DELETE CASCADE,
  role text NOT NULL CHECK (role IN ('adopted','candidate')),
  via_from_table bigint, via_to_table bigint, reasons text[] NOT NULL DEFAULT '{}'
);
CREATE TABLE doc.cell_group (group_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  table_id bigint NOT NULL REFERENCES doc.table_block ON DELETE CASCADE, kind text NOT NULL);
CREATE TABLE doc.cell_group_member (group_id bigint NOT NULL REFERENCES doc.cell_group ON DELETE CASCADE,
  cell_id bigint NOT NULL REFERENCES doc.grid_cell ON DELETE CASCADE, position int NOT NULL,
  PRIMARY KEY (group_id, cell_id));

-- 칸 안의 표·글 ---------------------------------------------------------------
CREATE TABLE doc.cell_container (
  parent_cell_id bigint PRIMARY KEY REFERENCES doc.grid_cell ON DELETE CASCADE,
  containment_state   text NOT NULL CHECK (containment_state   IN ('linked','unresolved','not_assessed')),
  coverage_state      text NOT NULL CHECK (coverage_state      IN ('complete','unresolved','not_assessed')),
  reading_order_state text NOT NULL CHECK (reading_order_state IN ('total','partial','unresolved','not_assessed')),
  duplicate_of_children boolean NOT NULL DEFAULT false
);
CREATE TABLE doc.container_item (
  parent_cell_id bigint NOT NULL REFERENCES doc.cell_container ON DELETE CASCADE,
  position int NOT NULL,
  item_kind text NOT NULL CHECK (item_kind IN ('table','content','native_table')),
  table_id bigint REFERENCES doc.table_block, content_id bigint REFERENCES doc.content,
  source_table_ref text,
  PRIMARY KEY (parent_cell_id, position)
);
CREATE TABLE doc.container_label (
  parent_cell_id bigint NOT NULL REFERENCES doc.cell_container ON DELETE CASCADE,
  position int NOT NULL, label_cell_id bigint NOT NULL REFERENCES doc.grid_cell,
  PRIMARY KEY (parent_cell_id, position)
);

-- 본문과 표의 연결, 출처 -----------------------------------------------------------
CREATE TABLE doc.context_link (
  link_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id bigint NOT NULL REFERENCES doc.document_run ON DELETE CASCADE,
  from_kind text NOT NULL CHECK (from_kind IN ('table','cell','node','container')), from_id bigint NOT NULL,
  to_content_id bigint NOT NULL REFERENCES doc.content ON DELETE CASCADE,
  relation text NOT NULL CHECK (relation IN ('note','caption','unit','formula','scope','outer_prose')),
  scope text NOT NULL, state text NOT NULL, decision_source text CHECK (decision_source IN ('rule','model'))
);
CREATE INDEX context_link_from ON doc.context_link (from_kind, from_id);
CREATE TABLE doc.source_ref (                          -- 순서 있는 원문 참조
  run_id bigint NOT NULL REFERENCES doc.document_run ON DELETE CASCADE,
  owner_kind text NOT NULL CHECK (owner_kind IN ('cell','content','node')), owner_id bigint NOT NULL,
  position int NOT NULL, page_no int NOT NULL,
  x0 real, y0 real, x1 real, y1 real,
  block_ref text, span_ref text, cell_ref text, char_start int, char_end int,
  PRIMARY KEY (owner_kind, owner_id, position)
);

-- 색인(서빙 표에서 다시 만들 수 있다) -----------------------------------------------
CREATE TABLE idx.search_entry (
  entry_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id bigint NOT NULL REFERENCES doc.document_run ON DELETE CASCADE,
  kind text NOT NULL CHECK (kind IN ('paragraph','note','table','table_row','cell_text','table_cell_chunk')),
  search_text text NOT NULL, primary_start int, primary_end int, join_separator text,
  projection_version text NOT NULL,
  search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple', search_text)) STORED
);
CREATE INDEX search_entry_fts ON idx.search_entry USING gin (search_vector);
CREATE INDEX search_entry_run ON idx.search_entry (run_id);
CREATE TABLE idx.search_entry_source (
  entry_id bigint NOT NULL REFERENCES idx.search_entry ON DELETE CASCADE, position int NOT NULL,
  role text NOT NULL CHECK (role IN ('hit','row_neighbor','context')),
  ref_kind text NOT NULL CHECK (ref_kind IN ('content','cell','node','table')), ref_id bigint NOT NULL,
  source_start int, source_end int, entry_start int, entry_end int,
  PRIMARY KEY (entry_id, position)
);
CREATE TABLE idx.embedding_profile (
  profile_id int GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  provider text NOT NULL, model text NOT NULL, dimensions int NOT NULL,
  profile_version text NOT NULL, input_version text NOT NULL,
  UNIQUE (provider, model, dimensions, profile_version, input_version)
);
CREATE TABLE idx.entry_embedding (                     -- 모델(프로필)마다 파티션을 두고 그 차원의 HNSW 색인을 건다
  entry_id bigint NOT NULL REFERENCES idx.search_entry ON DELETE CASCADE,
  profile_id int NOT NULL REFERENCES idx.embedding_profile,
  input_sha256 char(64) NOT NULL,                      -- 벡터를 만든 search_text의 해시
  embedding vector NOT NULL,
  PRIMARY KEY (profile_id, entry_id)
) PARTITION BY LIST (profile_id);

-- 이어 보는 조회 ----------------------------------------------------------------
CREATE VIEW doc.v_cell_path AS
WITH RECURSIVE up AS (
  SELECT node_id AS leaf, node_id, parent_id, label_text, 1 AS depth FROM doc.axis_node
  UNION ALL
  SELECT up.leaf, n.node_id, n.parent_id, n.label_text, up.depth + 1
  FROM up JOIN doc.axis_node n ON n.node_id = up.parent_id
), path AS (
  SELECT leaf, string_agg(label_text, ' > ' ORDER BY depth DESC) AS path FROM up GROUP BY leaf
)
SELECT d.display_name, t.page_no, lt.title AS table_title, r.path AS row_path, c.path AS column_path,
       g.text_raw AS value, p.path_state, p.lookup_eligible, p.withhold_reason, g.cell_id
FROM doc.cell_path p
JOIN doc.grid_cell g USING (cell_id)
JOIN doc.table_block t USING (table_id)
JOIN doc.logical_table lt USING (logical_table_id)
JOIN doc.document_run ru ON ru.run_id = t.run_id AND ru.is_current
JOIN doc.document d USING (document_id)
LEFT JOIN path r ON r.leaf = p.row_node_id
LEFT JOIN path c ON c.leaf = p.col_node_id;
```

저장할 때 검사하는 것(트랜잭션 안에서, 어기면 그 문서의 적재를 되돌린다):

- 칸의 행·열 노드는 그 칸이 속한 논리 표의 노드이고 `panel_no`가 칸과 같다.
- 다른 조각의 노드를 가리키는 칸은 `cell_path_source`에 거친 연결이 있고, 그 연결이 `candidate`이면 `path_state`가 `observed`가 아니다.
- `container_item`의 표는 부모 칸과 같은 쪽에 있다. 세 상태가 모두 충족이 아니면 부모 문맥을 쓰는 검색 항목을 만들지 않는다.
- 쪽·표·본문의 `position`은 쪽 안에서 겹치지 않는다.
