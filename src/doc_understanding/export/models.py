# 역할: 정리된 문서 출력의 규격(필드와 값의 범위). 출력은 이 모델로 검증하고, 스키마 파일도 여기서 만든다.
"""정리된 문서 출력 규격 `tabledoc/0.5`.

파서가 문서 하나를 읽은 결과를 관리하기 좋은 형태로 고정한다(ADR-080). 한 번씩만 담고, 번호로 잇는다.

- 번호는 문서 안에서만 통한다. 표는 문서의 표 순서, 칸은 표 안의 칸 순서, 노드는 행 트리 다음 열 트리 순서,
  본문은 읽는 순서로 1부터 매긴다. 같은 분석 결과에서는 항상 같은 번호가 나온다.
  다시 분석한 결과끼리 맞출 때는 번호가 아니라 `ref`(쪽·표·칸의 원래 식별자)를 쓴다.
- 경로는 복사하지 않는다. 값 칸은 행 잎 노드와 열 잎 노드만 가리키고, 경로는 `parent`를 따라 읽는다.
- 라벨은 원문(`label_raw`)과 붙여 쓴 것(`label`) 두 가지다. 원문은 고치지 않는다.
- 상태는 분석이 낸 값을 그대로 옮긴다. `observed`는 원문에서 직접 본 것, `inferred`는 규칙으로 추론한 것,
  `unresolved`는 정하지 못한 것이다. 이 출력은 사람이 확인한 사실이 아니다(`facts_published`는 항상 false).
- 글자의 출처: 쪽의 `text_source`가 `native`면 PDF에 있던 글자, `ocr`이면 그림에서 읽은 글자다.
  OCR로 읽은 칸·본문에는 `confidence`가 붙는다.
- 섹션은 제목 한 줄과 그 아래의 본문·표 묶음이다. 쪽과는 별개여서 한 섹션이 여러 쪽에 걸칠 수 있고, 한 쪽에
  여러 섹션이 있을 수 있다. 항목의 `section_id`가 소속이며, 규칙이 정하지 못한 항목에는 없다(추측해 채우지 않는다).
  상위 섹션은 `parent`를 따라 읽는다.

0.2 → 0.3: `sections` 목록, 항목의 `section_id`, 표 항목의 `context`를 추가했다. 기존 필드는 그대로다.
0.3 → 0.4: 표 연결의 상태에 `accepted`(규칙이 채택함, 사람이 확인한 것은 아님)를 더하고, 연결마다 빌려 온 머리글 칸
(`header_cells`)·경쟁하는 앞 표(`candidate_tables`)·값 칸별 출처(`paths`)를 담았다. 다른 쪽의 칸은 `[표 번호, 칸 번호]`로
가리킨다. 섹션에는 다음 쪽에 다시 인쇄된 제목 줄(`reprinted_title_content_ids`)을 더했다. 기존 필드는 그대로다.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FORMAT = "tabledoc/0.5"
CellRef = tuple[int, int]  # 표 번호, 그 표 안의 칸 번호. 다른 표(다른 쪽)의 칸을 가리킬 때 쓴다
State = Literal["observed", "inferred", "unresolved"]
LinkState = Literal["resolved", "ambiguous", "unresolved"]
BBox = tuple[float, float, float, float]  # x0, y0, x1, y1. 쪽의 왼쪽 위가 원점, 단위는 PDF 포인트


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class DocumentInfo(Model):
    document_id: str = Field(description="원본 내용 해시로 만든 문서 식별자. 출력 전체에서 여기에만 있다")
    pages: int
    tables: int
    validation_status: Literal["not_validated", "partial", "review_required", "validated"]
    facts_published: Literal[False] = False


class Ocr(Model):
    engine: str
    model: str
    dpi: int
    mean_confidence: float | None = None


class Item(Model):
    """쪽 안의 읽는 순서 한 줄. 본문이면 글자를, 표면 표 번호를 가진다."""
    position: int = Field(ge=1)
    kind: Literal["paragraph", "note", "rule", "image", "unknown", "table"]
    content_id: int | None = None
    table_id: int | None = None
    text: str | None = None
    bbox: BBox | None = None
    section_id: int | None = Field(default=None, description="이 항목이 속한 섹션. 정하지 못했으면 없다")
    section_title: bool | None = Field(default=None, description="이 줄이 섹션의 제목이면 true")
    context: list[int] | None = Field(default=None, description="표 항목에만: 이 표를 설명한다고 본 본문 줄(content_id)")
    inside_tables: list[int] | None = Field(default=None, description="표 안의 글자를 다시 뽑은 줄이면 그 표들. 본문으로 읽지 않는다")
    confidence: float | None = None
    cell_assignment: Literal["ambiguous"] | None = Field(
        default=None, description="OCR 줄이 표의 두 칸에 걸치거나 경계에 놓여 칸을 정하지 못했을 때만 있다. 글자는 어느 칸에도 넣지 않았다")


class Section(Model):
    """제목 한 줄 아래의 묶음. 구성원은 항목의 `section_id`로 찾는다."""
    section_id: int
    parent: int | None = Field(description="상위 섹션. 최상위면 null")
    title: str = Field(description="제목 원문")
    title_content_id: int | None = Field(description="제목이 인쇄된 본문 줄")
    first_page: int | None = Field(description="구성원이 있는 첫 쪽")
    last_page: int | None = Field(description="구성원이 있는 마지막 쪽. 첫 쪽과 다르면 쪽을 넘어간다")
    reprinted_title_content_ids: list[int] = Field(default=[], description="같은 제목이 다음 쪽에 다시 인쇄된 줄. 새 섹션이 아니다")
    state: Literal["inferred", "unresolved"]


class Page(Model):
    page_no: int | None
    width: float | None = None
    height: float | None = None
    text_source: Literal["native", "ocr", "mixed", "none"] | None = None
    ocr: Ocr | None = None
    items: list[Item]


class Panel(Model):
    panel_no: int
    first_col: int
    last_col: int
    header_rows: int
    label_columns: int


class Node(Model):
    """머리글 트리의 노드. 부모를 가리킨다."""
    node_id: int
    parent: int | None
    panel_no: int = 0
    label_raw: str | None
    label: str | None
    cell_id: int | None = Field(description="이 라벨이 인쇄된 칸")
    state: State


class Cell(Model):
    """표의 칸 하나. 머리글·라벨·값·빈칸을 모두 담는다."""
    cell_id: int
    row: int | None
    col: int | None
    rowspan: int | None
    colspan: int | None
    role: Literal["corner", "row_label", "column_header", "value", "unknown"]
    text: str | None
    bbox: BBox
    status: Literal["inferred", "unresolved"] | None = Field(default=None, description="격자 복원이 확실하지 않을 때만 있다")
    node: int | None = Field(default=None, description="이 칸이 머리글·라벨이면 그 노드")
    confidence: float | None = None


class Path(Model):
    """값 칸의 채택된 경로. 잎 노드 두 개."""
    cell_id: int
    row_node: int | None
    column_node: int | None
    state: State


class OpenQuestion(Model):
    code: str
    reason: str


class ParentCell(Model):
    table_id: int = Field(ge=1)
    cell_id: int = Field(ge=1)
    state: Literal["observed", "inferred"]


class Table(Model):
    parent_cell: ParentCell | None = None
    table_id: int
    ref: str = Field(description="원래 식별자(쪽/표). 다시 분석한 결과끼리 맞출 때 쓴다")
    page_no: int
    bbox: BBox
    rows: int
    columns: int
    state: Literal["candidate", "partial", "review_required", "invalid", "not_analyzed"]
    logical_table: int | None = Field(default=None, description="쪽을 넘어가는 표 조각을 묶는 번호")
    panels: list[Panel] = []
    row_nodes: list[Node] = []
    column_nodes: list[Node] = []
    paths: list[Path] = []
    open_questions: list[OpenQuestion] = []
    grid: list[Cell]


class Link(Model):
    """본문 한 줄과 표(또는 표의 행·열)의 관계."""
    relation: Literal["note", "caption", "unit", "formula"]
    scope: Literal["page", "section", "table", "row", "column", "cell", "unknown"]
    content_id: int | None
    state: LinkState
    table_id: int | None = None
    node: int | None = None
    target_ref: str | None = None


class LinkPath(Model):
    """이어지는 표(`to_table`)의 값 칸 하나가 빌려 쓰는 머리글의 출처. 바깥에서 안쪽 순서다."""
    cell_id: int = Field(description="`to_table`에서 이 연결이 값으로 해석하는 칸. 그 표의 격자에서는 값이 아닌 역할일 수 있으므로 "
                                     "연결의 `state`를 함께 읽는다(`candidate`면 채택되지 않은 해석이다)")
    row: list[CellRef] = []
    column: list[CellRef] = []
    row_candidates: list[CellRef] = Field(default=[], description="행 라벨 후보가 여럿이라 정하지 못했을 때의 후보")
    state: Literal["inferred", "unresolved"]
    reasons: list[str] | None = Field(default=None, description="연결의 `reasons`와 다를 때만 있다")


class TableLink(Model):
    """표 조각 사이의 연결. 조각은 합치지 않는다.

    `candidate`는 후보, `accepted`는 규칙이 채택한 것, `confirmed`·`rejected`는 사람이 판정한 것이다.
    """
    from_table: int | None
    to_table: int | None
    kind: Literal["detached_header", "page_continuation"]
    state: Literal["candidate", "accepted", "confirmed", "rejected"]
    reasons: list[str] = []
    header_cells: list[CellRef] = Field(default=[], description="앞 표에서 빌려 오는 열 머리글 칸")
    row_cells: list[CellRef] = Field(default=[], description="앞 표에서 이어받는 행 라벨 칸")
    candidate_tables: list[int] = Field(default=[], description="앞 표 후보가 여럿일 때 경쟁하는 표")
    paths: list[LinkPath] = []


class CleanDocument(Model):
    format: Literal["tabledoc/0.5"] = FORMAT
    document: DocumentInfo
    sections: list[Section]
    pages: list[Page]
    tables: list[Table]
    links: list[Link]
    table_links: list[TableLink]
