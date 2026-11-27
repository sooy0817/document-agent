# 역할: 기존 공용 프로토콜과 초안 계약을 유지한다. 실제 의미 후보 모델은 structure/models.py를 사용한다.
"""Physical grid contract implemented; semantic contracts remain proposed."""

from typing import Literal, Protocol
from uuid import UUID

from doc_understanding.domain.models import DocumentIR, EvidenceModel, Issue, NonEmpty, Positive, SourceRef
from doc_understanding.parser.physical.physical_models import PhysicalTableIR

AnalysisState = Literal["candidate", "validated", "partial", "review_required", "invalid"]
MappingStatus = Literal["resolved", "ambiguous", "unresolved"]


# 초기 초안의 구조 특성 계약이다. 현재 구조 분석 결과 모델은 structure/models.py와 구분한다.
class StructuralFeature(EvidenceModel):
    type: Literal[
        "multi_level_columns",
        "hierarchical_rows",
        "merged_cells",
        "side_by_side_panels",
        "continued_across_pages",
        "repeated_headers",
        "grouped_rows",
        "subtotals",
        "cross_tab",
        "mixed_table_narrative",
        "footnote_attached",
        "irregular_grid",
        "rotated_text",
    ]
    evidence_refs: tuple[SourceRef, ...]
    heuristic_id: NonEmpty
    disposition: Literal["observed", "candidate", "unknown"]


# 초기 초안에서 패널에 속한 셀 ID 집합을 표현한다.
class Panel(EvidenceModel):
    panel_id: NonEmpty
    cell_ids: tuple[str, ...]


# 초기 초안의 부모·원본 셀 참조 모델이다. 현재 행열 축 노드는 structure/models.py의 동명 모델을 사용한다.
class HierarchyNode(EvidenceModel):
    node_id: NonEmpty
    panel_id: NonEmpty
    label_raw: str
    parent_id: str | None
    source_cell_ids: tuple[str, ...]
    kind: Literal["header", "detail", "group", "subtotal", "total", "unknown"]


# 초기 초안에서 값 셀을 행 노드·헤더 말단에 연결하는 계약이다.
class CellMapping(EvidenceModel):
    value_cell_id: NonEmpty
    panel_id: str | None
    row_node_id: str | None
    header_leaf_id: str | None
    status: MappingStatus


# 주석·캡션·단위·수식의 출처와 적용 대상/범위를 후보로 연결한다.
class ContextLink(EvidenceModel):
    source: SourceRef
    target_id: str | None
    scope: Literal["page", "section", "table", "row", "column", "cell", "unknown"]
    relation: Literal["note", "caption", "unit", "formula"]
    status: MappingStatus


# 이 파일의 1.0 초안 모델이다. 기본 analyzer가 반환하는 2.0 계약과 혼동하지 않는다.
class StructuredTableIR(EvidenceModel):
    schema_version: Literal["structured-table-ir/1.0"] = "structured-table-ir/1.0"
    table_id: NonEmpty
    document_id: NonEmpty
    analysis_run_id: UUID
    analyzer_version: NonEmpty
    source_ir_hash: NonEmpty
    table_candidate_ids: tuple[str, ...]
    pages: tuple[Positive, ...]
    analysis_state: AnalysisState = "candidate"
    features: tuple[StructuralFeature, ...] = ()
    panels: tuple[Panel, ...] = ()
    column_hierarchy: tuple[HierarchyNode, ...] = ()
    row_hierarchy: tuple[HierarchyNode, ...] = ()
    cell_mappings: tuple[CellMapping, ...] = ()
    context_links: tuple[ContextLink, ...] = ()
    unresolved_regions: tuple[SourceRef, ...] = ()
    issues: tuple[Issue, ...] = ()


# 원본 문서·물리 표 후보·인접 페이지와 선택적 모델 호출 한도를 묶는 요청 계약이다.
class TableAnalysisRequest(EvidenceModel):
    document: DocumentIR
    candidates: tuple[PhysicalTableIR, ...]
    adjacent_page_refs: tuple[Positive, ...] = ()
    max_structure_llm_calls: Literal[0, 1] = 0


# 초안 구조 표와 미해결 영역·진단을 묶는 결과 계약이다.
class TableAnalysisResult(EvidenceModel):
    analysis_run_id: UUID
    structured_tables: tuple[StructuredTableIR, ...]
    unresolved_regions: tuple[SourceRef, ...]
    issues: tuple[Issue, ...]


# 물리 분석기의 호출 형식만 정의하는 프로토콜이다.
class PhysicalStructureAnalyzer(Protocol):
    # 요청에서 물리 표 결과를 반환하는 구현이 지켜야 할 인터페이스다.
    def analyze(self, request: TableAnalysisRequest) -> tuple[PhysicalTableIR, ...]: ...


# 물리 표에서 구조 특성을 분류하는 구현의 인터페이스다.
class StructuralFeatureClassifier(Protocol):
    # 표의 구조 특성 목록 반환 형식만 정의한다.
    def classify(self, table: PhysicalTableIR) -> tuple[StructuralFeature, ...]: ...


# 물리 표와 구조 특성을 초안 의미 구조에 연결하는 인터페이스다.
class SemanticStructureResolver(Protocol):
    # 표와 특성을 받아 초안 구조 결과를 반환하는 호출 계약이다.
    def resolve(
        self, table: PhysicalTableIR, features: tuple[StructuralFeature, ...]
    ) -> StructuredTableIR: ...


# 물리 입력과 초안 구조 결과의 검증 인터페이스다.
class StructureValidator(Protocol):
    # 검증 결과를 반영한 초안 구조 표의 반환 형식을 정의한다.
    def validate(
        self, physical: PhysicalTableIR, structured: StructuredTableIR
    ) -> StructuredTableIR: ...


# 요청부터 결과까지 연결하는 상위 분석 인터페이스다.
class TableStructureAnalyzer(Protocol):
    # 상위 요청·결과 계약을 정의한다. 실제 기본 실행은 pipeline과 structure/analyzer에 있다.
    def analyze(self, request: TableAnalysisRequest) -> TableAnalysisResult: ...
