# NH Document Parser

PDF 한 건을 페이지별 본문과 표가 읽기 순서대로 연결된 `tabledoc/0.5` JSON으로 변환한다.
계약서와 경영공시처럼 본문 사이에 표가 있는 문서에 쓸 수 있다.

Python 3.11 이상에서 파서 전용 가상환경을 만든 뒤 전달받은 wheel을 설치한다.
전체 개발 앱과 이 패키지는 같은 내부 파서 모듈을 사용하므로 각각 별도 환경에 설치한다.

```sh
python -m pip install nh_document_parser-0.1.2-py3-none-any.whl
nh-parse agreement.pdf -o agreement.json --text-output agreement.txt
```

Python에서 읽으면 같은 결과를 사전으로 받는다.

```python
from nh_document_parser import parse_pdf, render_text

doc = parse_pdf("agreement.pdf")
text = render_text(doc)
for page in doc["pages"]:
    for item in page["items"]:
        if item["kind"] == "table":
            print("표", item["table_id"])
        elif not item.get("inside_tables"):
            print(item.get("text"))
```

`pages[].items`는 읽기 순서 후보를 담고, 표 항목의 `table_id`는 최상위 `tables`의
같은 번호를 가리킨다. 각 표의 `grid[].text`에는 문장·숫자를 원문 문자열로 보존한다.
`paths`는 값 셀과 행·열 라벨 노드를 연결하고, 상위 라벨은 노드의 `parent`를 따라간다.
판단할 수 없는 경로와 자동 추론 상태도 출력한다.
중첩 표의 `parent_cell.table_id`와 `cell_id`는 바깥 표와 그 표의 `grid`에 있는 칸을 가리킨다.
PDF의 기하·원문 근거로 연결한 관계는 `inferred`다. 부모 후보를 출력 격자의 한 칸으로
좁히지 못하면 안쪽 표의 `open_questions`에 이유를 남긴다. 최상위 표는 `parent_cell`을 생략한다.
출력 JSON Schema는 패키지의 `nh_document_parser/clean-document.schema.json`에 있다.

기본 설치는 OCR을 하지 않는다. 네이티브 텍스트가 없는 쪽은 `text_source: "none"`으로 표시된다.
그런 쪽을 읽으려면 `pip install "nh_document_parser-0.1.2-py3-none-any.whl[ocr]"`로 엔진을 함께 설치하고,
환경변수 `NH_OCR_CONFIG`에 설정 파일 경로를 준다. 폐쇄망에서는 모델 파일을 미리 반입해 경로를 적는다.

```json
{"provider": "rapidocr", "language": "korean", "dpi": 200,
 "det_model": "D:/models/ch_PP-OCRv5_det_mobile.onnx", "rec_model": "D:/models/korean_PP-OCRv5_rec_mobile.onnx"}
```

엔진은 글자 없는 쪽이 있는 PDF에서만 만들어지므로, 글자가 모두 있는 PDF는 엔진이나 모델이 없어도 분석된다.
읽은 쪽은 `text_source: "ocr"`이고 칸·본문에 `confidence`가 붙는다. 표의 두 칸에 걸치거나 경계에 놓인 줄은
어느 칸에도 넣지 않고 `cell_assignment: "ambiguous"`인 본문 항목으로 남긴다. 스캔 쪽의 행·열 경로는
글자 없이 분석된 것이므로 확정된 구조로 쓰지 않는다.

기본 실행은 임시 폴더에서 처리한 뒤 중간 파일을 정리한다. 출처를 다시 점검하려면
`nh-parse agreement.pdf -o agreement.json --evidence-dir runs` 또는
`parse_pdf("agreement.pdf", evidence_dir="runs")`를 사용한다. 이 경우 새 실행 폴더에
입력 PDF 사본, 원문·좌표, 표 격자 및 구조 후보를 보관한다. CLI는 기존 출력 파일을 보호한다.

사람용 텍스트는 페이지 순서대로 본문과 표를 표시한다. 단순한 문자 머리글은 같은 행의 각 값 앞에 붙인다. 병합 머리글이나 숫자로만 된 머리글 등은 셀을 원래 행과 열 순서로 표시한다. 셀 안의 줄바꿈은 공백으로 정리한다.
