import json
from types import SimpleNamespace

import pytest

from doc_understanding.domain.models import BBox
from doc_understanding.parser.layout import diagnose, union_area
from doc_understanding.storage.staging.local import literal


def page(text="", images=(), drawings=0):
    blocks = [SimpleNamespace(kind="text", text_raw=text)] if text else []
    blocks += [SimpleNamespace(kind="image", source=SimpleNamespace(
        bbox=BBox(x0=a, y0=b, x1=c, y1=d))) for a, b, c, d in images]
    blocks += [SimpleNamespace(kind="drawing")] * drawings
    return SimpleNamespace(page_id="p1", page_number=1, width=100, height=100, blocks=blocks)


@pytest.mark.parametrize("p,kind,status", [
    (page("x" * 50), "native_text", "EXTRACTED"),
    (page(images=[(0, 0, 100, 100)]), "image_based", "OCR_REQUIRED"),
    (page("x" * 50, [(0, 0, 100, 100)]), "mixed", "OCR_REQUIRED"),
    (page(), "blank", "EMPTY"),
    (page(drawings=2), "undetermined", "REVIEW_REQUIRED"),
    (page("1"), "undetermined", "REVIEW_REQUIRED"),
    (page("x" * 50, [(0, 0, 40, 100)]), "undetermined", "REVIEW_REQUIRED"),
])
def test_route(p, kind, status):
    result = diagnose(p, SimpleNamespace(engine="test", engine_version="1"))
    assert (result.kind, result.status) == (kind, status)
    assert result.confidence is None


def test_image_overlap_and_clip():
    b = BBox(x0=0, y0=0, x1=10, y1=10)
    assert union_area([b, b]) == 100
    assert diagnose(page(images=[(-10, -10, 110, 110)]),
                    SimpleNamespace(engine="test", engine_version="1")).image_coverage == 1


def test_sql_literal_not_shell():
    assert literal("x'; DROP SCHEMA staging; --") == "'x''; DROP SCHEMA staging; --'"
    assert json.loads('{"raw":"△ -"}')["raw"] == "△ -"


def test_projection_preserves_original_and_unresolved_order(tmp_path):
    import pymupdf
    from doc_understanding.domain.contracts import ExtractionRequest
    from doc_understanding.parser.extraction.pymupdf_adapter import PyMuPDFExtractor
    from doc_understanding.parser.orchestration.workflow import extract_ir
    from doc_understanding.parser.layout import DocumentLayoutProjection, project
    pdf = pymupdf.open()
    p = pdf.new_page()
    p.insert_text((50, 50), "Title and paragraph remain original.")
    p.insert_text((50, 100), "Second paragraph with value 123.45")
    ir = extract_ir(ExtractionRequest(pdf.tobytes(), "synthetic.pdf"),
                    PyMuPDFExtractor(), tmp_path)
    raw = ir.model_dump_json().encode()
    layout = project(ir, raw)
    assert ir.model_dump_json().encode() == raw
    assert len(layout.blocks) == sum(len(p.blocks) for p in ir.pages)
    for b, source in zip(layout.blocks, ir.pages[0].blocks):
        assert b.text_raw == source.text_raw
        assert b.bbox == source.source.bbox
        assert b.extraction_order == source.extraction_order
        assert b.semantic_parent_state == "unresolved"
    assert all(r.state == "unresolved" for r in layout.relations)
    assert DocumentLayoutProjection.model_validate_json(layout.model_dump_json()) == layout


def test_changed_source_rejected_before_database(tmp_path, monkeypatch):
    from doc_understanding.storage.staging import local
    p = tmp_path / "original.pdf"
    p.write_bytes(b"modified")
    monkeypatch.setattr(local, "query", lambda _: pytest.fail("must not touch DB"))
    with pytest.raises(ValueError, match="SOURCE_CHANGED"):
        local.ingest(SimpleNamespace(source_sha256="0" * 64), None, tmp_path,
                     p, "key", "run", [])
