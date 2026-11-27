# 역할: DocumentIR의 페이지·블록·출처를 배치 보기로 투영한다. 기하 순서는 의미상 읽기 순서 확정이 아니다.
"""Lossless reference projection, not a semantic document hierarchy."""
from hashlib import sha256
from typing import Literal

from doc_understanding.domain.models import BBox, DocumentIR, EvidenceModel


# 텍스트량·이미지 피복으로 페이지 처리 경로와 진단 근거를 기록한다.
class PageDiagnosis(EvidenceModel):
    page_id: str
    page_number: int
    kind: Literal["native_text", "image_based", "mixed", "blank", "undetermined"]
    status: Literal["EXTRACTED", "OCR_REQUIRED", "EMPTY", "REVIEW_REQUIRED"]
    text_characters: int
    image_coverage: float
    drawing_count: int
    reasons: tuple[str, ...]
    engine: str
    engine_version: str
    confidence: None = None
    policy_version: str = "page-route/0.1"


# 원본 블록의 좌표·텍스트·추출 순서·JSON 위치를 보존하는 배치 항목이다.
class LayoutBlock(EvidenceModel):
    block_id: str
    page_id: str
    page_number: int
    kind: str
    bbox: BBox
    extraction_order: int
    text_raw: str | None
    parent_ref: str
    semantic_parent_state: Literal["unresolved"] = "unresolved"
    role_candidates: tuple[str, ...] = ()
    original_pointer: str


# 기하상 다음 블록, 표와 겹치는 텍스트, 표 산출물의 참조 관계를 나타낸다.
class LayoutRelation(EvidenceModel):
    source: str
    target: str
    kind: Literal["geometric_next", "text_overlaps_table", "table_artifact"]
    state: Literal["inferred", "unresolved"] = "inferred"
    reason: str


# 원본 IR 해시와 페이지 진단·블록·관계 후보를 묶는다. 의미 검토와 사실 발행은 허용하지 않는다.
class DocumentLayoutProjection(EvidenceModel):
    schema_version: Literal["document-layout/0.1"] = "document-layout/0.1"
    document_id: str
    document_ir_sha256: str
    pages: tuple[PageDiagnosis, ...]
    blocks: tuple[LayoutBlock, ...]
    relations: tuple[LayoutRelation, ...]
    processing_status: Literal["EXTRACTED", "OCR_REQUIRED", "REVIEW_REQUIRED"]
    semantic_status: Literal["unreviewed"] = "unreviewed"
    fact_publication_allowed: Literal[False] = False
    limitations: tuple[str, ...] = (
        "Geometric order is a candidate, not validated reading order.",
        "Chapter/section/note scope is unresolved; no hierarchy is fabricated.",
        "Image coverage cannot distinguish photographs from scanned text.",
    )


# 겹치는 이미지 면적을 중복 계산하지 않도록 직사각형 합집합 면적을 구한다.
def union_area(boxes):
    """Exact rectangle union; overlapping images must not inflate coverage."""
    xs = sorted({x for b in boxes for x in (b.x0, b.x1)})
    area = 0.0
    for left, right in zip(xs, xs[1:]):
        ys = sorted((b.y0, b.y1) for b in boxes if b.x0 < right and b.x1 > left)
        end = float("-inf")
        length = 0.0
        for low, high in ys:
            length += max(0, high - max(low, end))
            end = max(end, high)
        area += (right - left) * length
    return area


# 페이지의 문자 수·이미지 피복·그리기 수로 추출/OCR 필요/검토 상태를 제안한다. OCR 자체를 실행하지 않는다.
def diagnose(page, run):
    text = sum(len((b.text_raw or "").strip()) for b in page.blocks if b.kind == "text")
    boxes = []
    for b in page.blocks:
        if b.kind == "image":
            x0, y0, x1, y1 = b.source.bbox.xyxy
            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(page.width, x1), min(page.height, y1)
            if x1 > x0 and y1 > y0:
                boxes.append(BBox(x0=x0, y0=y0, x1=x1, y1=y1))
    coverage = union_area(boxes) / (page.width * page.height)
    drawings = sum(b.kind == "drawing" for b in page.blocks)
    if coverage >= .5:
        kind, status = ("mixed" if text >= 20 else "image_based"), "OCR_REQUIRED"
    elif text >= 20 and coverage < .1:
        kind, status = "native_text", "EXTRACTED"
    elif text == 0 and not boxes and not drawings:
        kind, status = "blank", "EMPTY"
    else:
        kind, status = "undetermined", "REVIEW_REQUIRED"
    return PageDiagnosis(
        page_id=page.page_id, page_number=page.page_number, kind=kind, status=status,
        text_characters=text, image_coverage=coverage, drawing_count=drawings,
        reasons=(f"native_characters={text}", f"image_union_coverage={coverage:.6f}",
                 f"vector_blocks={drawings}", "routing heuristic; not OCR accuracy"),
        engine=run.engine, engine_version=run.engine_version,
    )


# 원본 참조와 해시를 유지한 채 블록 순서·텍스트/표 공유 span·산출물 연결을 배치 결과로 만든다.
def project(ir: DocumentIR, ir_bytes: bytes, table_links=()):
    blocks, relations = [], []
    pages = tuple(diagnose(p, ir.run) for p in ir.pages)
    for pi, page in enumerate(ir.pages):
        text_blocks = []
        for bi, block in enumerate(page.blocks):
            blocks.append(LayoutBlock(
                block_id=block.block_id, page_id=page.page_id, page_number=page.page_number,
                kind=block.kind, bbox=block.source.bbox, extraction_order=block.extraction_order,
                text_raw=block.text_raw, parent_ref=page.page_id,
                role_candidates=("paragraph", "heading", "note") if block.kind == "text" else (),
                original_pointer=f"/pages/{pi}/blocks/{bi}",
            ))
            if block.kind == "text":
                text_blocks.append(block)
                span_ids = {s.span_id for s in block.spans}
                for table in page.tables:
                    if span_ids.intersection(s for c in table.cells for s in c.span_refs):
                        relations.append(LayoutRelation(
                            source=block.block_id, target=table.table_candidate_id,
                            kind="text_overlaps_table",
                            reason="Shared original span references; preserve both text representations.",
                        ))
        ordered = sorted(text_blocks, key=lambda b: (b.source.bbox.y0, b.source.bbox.x0))
        for a, b in zip(ordered, ordered[1:]):
            relations.append(LayoutRelation(source=a.block_id, target=b.block_id,
                kind="geometric_next", state="unresolved",
                reason="Top-to-bottom geometry only; multi-column order unverified."))
    relations.extend(LayoutRelation(**link) for link in table_links)
    status = ("OCR_REQUIRED" if any(p.status == "OCR_REQUIRED" for p in pages) else
              "REVIEW_REQUIRED" if any(p.status == "REVIEW_REQUIRED" for p in pages)
              else "EXTRACTED")
    return DocumentLayoutProjection(
        document_id=ir.document_id, document_ir_sha256=sha256(ir_bytes).hexdigest(),
        pages=pages, blocks=tuple(blocks), relations=tuple(relations), processing_status=status,
    )
