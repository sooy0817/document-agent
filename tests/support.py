"""Synthetic IR fixture; NOT an extracted or reviewed NH financial statement."""
from hashlib import sha256

from doc_understanding.domain.models import (
    ArtifactRef, BBox, BlockIR, CellIR, DocumentIR, ExtractionRun, PageIR,
    RasterRef, SourceRef, TableCandidateIR, TextSpanIR,
)


def sample_ir(run=None, raw_values=("", "0", "-1", "△2", "-", "66,224"), data=b"%PDF-fixture"):
    digest = sha256(data).hexdigest()
    did = f"sha256:{digest}"
    bbox = BBox(x0=0, y0=0, x1=100, y1=100)
    spans, cells = [], []
    for index, text in enumerate(raw_values):
        sid, cid = f"s{index}", f"c{index}"
        source = SourceRef(document_id=did, page_number=1, bbox=bbox, block_id="b1", span_id=sid)
        spans.append(TextSpanIR(span_id=sid, source=source, text_raw=text, line_index=index,
                                font="fixture", font_size=10, flags=0))
        cells.append(CellIR(cell_id=cid,
                            source=SourceRef(document_id=did, page_number=1, bbox=bbox, cell_id=cid),
                            row_index=index, column_index=0, text_raw=text, span_refs=(sid,),
                            extraction_status="blank" if text == "" else "observed"))
    table = TableCandidateIR(table_candidate_id="t1", source=SourceRef(document_id=did,
                            page_number=1, bbox=bbox), cells=tuple(cells),
                            detector_grid=tuple((c.cell_id,) for c in cells),
                            row_count=len(cells), column_count=1)
    page = PageIR(page_id="p1", page_number=1, width=100, height=100, original_rotation=0,
                  original_cropbox=bbox, evidence_sha256="a"*64,
                  image=RasterRef(artifact=ArtifactRef(path="pages/1.png", sha256="b"*64,
                                 media_type="image/png"), width_pixels=100, height_pixels=100,
                                 dpi=72, page_to_pixel=(1,0,0,1,0,0), clip=bbox),
                  blocks=(BlockIR(block_id="b1", kind="text", extraction_order=0,
                                  source=SourceRef(document_id=did, page_number=1, bbox=bbox,
                                                   block_id="b1"), spans=tuple(spans)),),
                  tables=(table,), text_status="native")
    return DocumentIR(document_id=did, source_name="synthetic.pdf", source_sha256=digest,
                      source=ArtifactRef(path="source.pdf", sha256=digest, media_type="application/pdf"),
                      pages=(page,), run=run or ExtractionRun(parser_version="test", engine="fake",
                                                             engine_version="1"), processing_status="extracted")
