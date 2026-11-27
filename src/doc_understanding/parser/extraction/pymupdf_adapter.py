# 동결 경계: PDF 원문·좌표·이미지·추출 출처를 보존한다. 행열 트리 개선을 위해 이 추출 결과를 수정하지 않는다.
"""Native evidence adapter; no semantic interpretation, normalization, or LLM calls."""
from __future__ import annotations

import hashlib
import json
import math

import pymupdf

from doc_understanding.domain.contracts import ArtifactStore, ExtractionError, ExtractionRequest
from doc_understanding.domain.models import BBox, BlockIR, CellIR, DocumentIR, DrawingCommand, ExtractionRun, Issue, PageIR, RasterRef, SourceRef, TableCandidateIR, TextSpanIR


# PyMuPDF 좌표 네 개를 공통 BBox로 옮긴다.
def box(coords) -> BBox:
    return BBox(x0=coords[0], y0=coords[1], x1=coords[2], y1=coords[3])


# PDF 원문·페이지·표 후보를 DocumentIR로 변환하는 추출 어댑터다.
class PyMuPDFExtractor:
    name = "pymupdf"
    version = pymupdf.VersionBind

    # 입력 제한과 PDF 유효성을 검사하고 원본 파일·페이지 산출물을 저장해 DocumentIR을 반환한다.
    def extract(self, request: ExtractionRequest, store: ArtifactStore,
                run: ExtractionRun) -> DocumentIR:
        if len(request.pdf_bytes) > request.limits.max_bytes:
            raise ExtractionError("FILE_TOO_LARGE")
        try:
            doc = pymupdf.open(stream=request.pdf_bytes, filetype="pdf")
        except (RuntimeError, ValueError) as exc:
            raise ExtractionError("INVALID_PDF") from exc
        with doc:
            if doc.needs_pass:
                raise ExtractionError("ENCRYPTED_PDF")
            if not doc.page_count:
                raise ExtractionError("EMPTY_PDF")
            if doc.page_count > request.limits.max_pages:
                raise ExtractionError("TOO_MANY_PAGES")
            digest = hashlib.sha256(request.pdf_bytes).hexdigest()
            document_id = f"sha256:{digest}"
            original = store.write("source.pdf", request.pdf_bytes, "application/pdf")
            pages = tuple(self._page(page, document_id, request, store) for page in doc)
        issues = tuple(i for p in pages for i in p.issues)
        return DocumentIR(
            document_id=document_id, source_name=request.source_name,
            source_sha256=digest, source=original, metadata=request.metadata,
            pages=pages, run=run, issues=issues,
            processing_status="review_required" if any(i.severity != "info" for i in issues)
            else "extracted",
        )

    # 페이지의 텍스트·그리기·이미지·표 후보와 추출 진단을 원본 좌표·출처에 연결한다.
    def _page(self, page, document_id, request, store) -> PageIR:
        number = page.number + 1
        prefix = f"{document_id}/p{number:04d}"
        rotation, cropbox = page.rotation, box(page.cropbox)
        # Only the in-memory view changes. The retained source.pdf is byte-identical.
        page.set_rotation(0)
        width, height = page.rect.width, page.rect.height
        scale = request.limits.render_dpi / 72
        if math.ceil(width * scale) * math.ceil(height * scale) > request.limits.max_pixels:
            raise ExtractionError("PAGE_PIXEL_LIMIT")
        pix = page.get_pixmap(dpi=request.limits.render_dpi, alpha=False)
        raster = RasterRef(
            artifact=store.write(f"pages/{number:04d}.png", pix.tobytes("png"), "image/png"),
            width_pixels=pix.width, height_pixels=pix.height,
            dpi=request.limits.render_dpi,
            page_to_pixel=(scale, 0, 0, scale, -pix.x, -pix.y), clip=box(page.rect),
        )
        data = page.get_text("dict", sort=False)
        blocks = []
        spans = []
        issues = []
        # 현재 문서·페이지에 속한 좌표와 객체 ID로 SourceRef를 만든다.
        def ref(bbox, **ids):
            return SourceRef(document_id=document_id, page_number=number, bbox=box(bbox), **ids)

        for index, native in enumerate(data["blocks"]):
            block_id = f"{prefix}/b{index:04d}"
            source = ref(native["bbox"], block_id=block_id)
            if native["type"] == 0:
                text_spans = []
                lines = []
                for li, line in enumerate(native["lines"]):
                    lines.append("".join(s["text"] for s in line["spans"]))
                    for si, span in enumerate(line["spans"]):
                        sid = f"{block_id}/l{li:04d}s{si:04d}"
                        text_spans.append(TextSpanIR(
                            span_id=sid, source=ref(span["bbox"], block_id=block_id, span_id=sid),
                            text_raw=span["text"], line_index=li, font=span["font"],
                            font_size=span["size"], flags=span["flags"],
                            direction=tuple(line["dir"]),
                        ))
                spans.extend(text_spans)
                blocks.append(BlockIR(block_id=block_id, kind="text", source=source,
                                      extraction_order=index, text_raw="\n".join(lines),
                                      spans=tuple(text_spans)))
            elif native["type"] == 1:
                ext = native["ext"]
                image = store.write(f"images/{number:04d}-{index:04d}.{ext}", native["image"],
                                    "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}")
                blocks.append(BlockIR(block_id=block_id, kind="image", source=source,
                                      extraction_order=index, image=image))

        for di, drawing in enumerate(page.get_drawings()):
            commands = []
            for item in drawing["items"]:
                numbers = []
                for part in item[1:]:
                    if isinstance(part, (int, float)):
                        numbers.append(float(part))
                    else:
                        for value in part:
                            if isinstance(value, (int, float)):
                                numbers.append(float(value))
                            else:
                                numbers.extend(float(v) for v in value)
                commands.append(DrawingCommand(operator=item[0], coordinates=tuple(numbers)))
            bid = f"{prefix}/d{di:04d}"
            blocks.append(BlockIR(block_id=bid, kind="drawing",
                                  source=ref(drawing["rect"], block_id=bid),
                                  extraction_order=len(blocks), drawing_commands=tuple(commands),
                                  drawing_stroked="s" in drawing["type"]))

        # Conservative line-based candidate detection; no claimed borderless-table coverage.
        try:
            tables = self._tables(page, prefix, spans, ref)
        except Exception:
            tables = ()
            issues.append(Issue(code="TABLE_DETECTION_FAILED", severity="warning",
                                message="Native table detection failed; page evidence retained."))
        for ti, table in enumerate(tables):
            bid = f"{prefix}/tb{ti:04d}"
            blocks.append(BlockIR(block_id=bid, kind="table_candidate",
                                  source=ref(table.source.bbox.xyxy, block_id=bid),
                                  extraction_order=len(blocks), table_ref=table.table_candidate_id))
            issues.extend(table.issues)
        has_text = any(s.text_raw.strip() for s in spans)
        has_visual = any(b.kind in ("image", "drawing") for b in blocks)
        text_status = "native" if has_text else "ocr_required" if has_visual else "empty"
        if text_status == "ocr_required":
            issues.append(Issue(code="OCR_REQUIRED", severity="warning",
                                message="No native text; OCR/vision is not implemented in Phase 1."))
        if has_text and any(b.kind == "image" for b in blocks):
            issues.append(Issue(code="IMAGE_TEXT_NOT_EVALUATED", severity="warning",
                                message="Embedded images may contain unextracted text."))
        if any("\ufffd" in s.text_raw for s in spans):
            issues.append(Issue(code="UNMAPPED_GLYPH", severity="warning",
                                message="Replacement glyph in native text; review original image."))
        evidence = {"blocks": [b.model_dump(mode="json") for b in blocks],
                    "tables": [t.model_dump(mode="json") for t in tables]}
        evidence_hash = hashlib.sha256(json.dumps(evidence, sort_keys=True,
                                                  ensure_ascii=False).encode()).hexdigest()
        return PageIR(page_id=prefix, page_number=number, width=width, height=height,
                      original_rotation=rotation, original_cropbox=cropbox,
                      evidence_sha256=evidence_hash, image=raster, blocks=tuple(blocks),
                      tables=tuple(tables), text_status=text_status, issues=tuple(issues))

    # 추출기가 찾은 표·셀 후보를 원문 span·좌표와 연결한다. 이 결과는 물리 격자 재구성의 입력이다.
    def _tables(self, page, prefix, spans, ref):
        result = []
        for ti, table in enumerate(page.find_tables().tables):
            tid = f"{prefix}/t{ti:04d}"
            raw = table.extract()
            cells, grid, by_bbox = [], [], {}
            unknown_slots = False
            for ri, row in enumerate(table.rows):
                ids = []
                for ci, coords in enumerate(row.cells):
                    if coords is None:
                        ids.append(None)
                        unknown_slots = True
                        continue
                    key = tuple(coords)
                    if key not in by_bbox:
                        cid = f"{tid}/c{len(cells):04d}"
                        by_bbox[key] = cid
                        text = raw[ri][ci]
                        linked = tuple(s.span_id for s in spans if
                                       coords[0] <= (s.source.bbox.x0+s.source.bbox.x1)/2 <= coords[2]
                                       and coords[1] <= (s.source.bbox.y0+s.source.bbox.y1)/2 <= coords[3])
                        cells.append(CellIR(
                            cell_id=cid, source=ref(coords, cell_id=cid),
                            row_index=ri, column_index=ci, text_raw=text, span_refs=linked,
                            extraction_status="unreadable" if text is None else
                            "blank" if text == "" else "observed",
                        ))
                    ids.append(by_bbox[key])
                grid.append(tuple(ids))
            table_issues = (Issue(code="GRID_SPANS_UNRESOLVED", severity="warning",
                                  message="Null detector slots need Phase 2 physical grid analysis."),) if unknown_slots else ()
            result.append(TableCandidateIR(table_candidate_id=tid, source=ref(table.bbox),
                                           cells=tuple(cells), detector_grid=tuple(grid),
                                           row_count=table.row_count, column_count=table.col_count,
                                           issues=table_issues))
        return tuple(result)
