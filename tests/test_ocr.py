"""글자 없는 쪽의 OCR: 엔진 없이 검증하는 부분(좌표 변환, 칸 배정, 설정)과 엔진이 설치돼 있을 때만 도는 부분."""
import json

import pytest

from doc_understanding.ocr import OcrLine, OcrSettings, assign, configured, load, read_image, same_text


class FakeEngine:
    name, model = "fake", "fake-model"

    def __init__(self, lines):
        self.lines = lines

    def read(self, image):
        return self.lines


def line(text, x0, y0, x1, y1, confidence=0.99):
    return OcrLine(text=text, confidence=confidence, bbox=(x0, y0, x1, y1))


def test_pixel_boxes_become_pdf_points_and_text_is_kept_as_read():
    engine = FakeEngine([([(100, 200), (300, 200), (300, 250), (100, 250)], "∆280", 0.97),
                         ([(400, 200), (500, 210), (495, 260), (398, 250)], "합 계", 1.2)])
    page = read_image(engine, "page.png", page_no=3, dpi=200, origin=(10.0, 20.0))
    assert (page.page_no, page.engine, page.model, page.dpi) == (3, "fake", "fake-model", 200)
    assert page.lines[0].text == "∆280" and page.lines[0].bbox == (46.0, 92.0, 118.0, 110.0)
    # 기울어진 네 꼭짓점은 감싸는 상자로, 범위를 벗어난 신뢰도는 0~1로 자른다.
    assert page.lines[1].bbox == (153.28, 92.0, 190.0, 113.6) and page.lines[1].confidence == 1.0
    assert page.mean_confidence == 0.985
    assert read_image(FakeEngine([]), "blank.png", page_no=1, dpi=200).mean_confidence is None


def test_lines_go_to_the_smallest_cell_holding_their_centre_and_read_top_to_bottom():
    boxes = [(0, 0, 100, 100), (40, 40, 100, 100), (100, 0, 200, 100)]  # 둘째는 첫째 칸 안의 표의 칸
    lines = [line("둘째 줄", 5, 20, 35, 30, 0.80), line("첫째", 5, 5, 20, 15), line("줄", 22, 6, 30, 15, 0.95),
             line("1,234", 50, 50, 90, 60), line("∆280", 110, 10, 150, 20), line("쪽 번호", 300, 300, 340, 310)]
    cells, loose, unsure = assign(lines, boxes)
    assert unsure == []
    assert cells[0]["text"] == "첫째 줄\n둘째 줄" and cells[0]["confidence"] == 0.80 and cells[0]["lines"] == 3
    assert cells[1]["text"] == "1,234" and cells[1]["marks"] == []
    # 삼각형이 다른 글자로 읽혀도 글자는 그대로 두고 표시만 한다.
    assert cells[2]["text"] == "∆280" and cells[2]["marks"] == ["NEGATIVE_MARK_VARIANT"]
    assert [x.text for x in loose] == ["쪽 번호"]
    empty, _, _ = assign([], boxes)
    assert empty[0] == dict(text="", confidence=None, lines=0, marks=[])


def test_a_line_over_two_cells_or_on_their_border_is_held_instead_of_given_to_the_first():
    boxes = [(0, 0, 100, 50), (100, 0, 200, 50)]
    across = line("구분 금액", 20, 10, 180, 30)      # 두 칸 전체에 걸친 줄, 중심이 경계 위
    leaning = line("합계 1,234", 30, 10, 130, 30)   # 중심은 첫 칸이지만 30%가 옆 칸에 있다
    nudged = line("금액", 96, 10, 160, 30)           # 선을 조금 넘었을 뿐이다(6%)
    cells, loose, unsure = assign([across, leaning, nudged], boxes)
    assert unsure == [across, leaning] and loose == []
    assert cells[0]["text"] == "" and cells[1]["text"] == "금액"
    # 안쪽 표의 칸에 든 줄이 바깥 칸에도 겹치는 것은 걸친 것이 아니다.
    cells, _, unsure = assign([line("1,234", 50, 50, 90, 60)], [(0, 0, 100, 100), (40, 40, 100, 100)])
    assert cells[1]["text"] == "1,234" and unsure == []


def test_comparison_ignores_spacing_width_and_triangle_shape_only():
    assert same_text("△ 1,234", "▲1,234") and same_text("１２３", "123") and same_text("구 분", "구분")
    assert not same_text("△280", "280") and not same_text("1,234", "1,284")


def test_settings_come_from_a_file_and_none_means_no_ocr(tmp_path, monkeypatch):
    monkeypatch.setenv("NH_OCR_CONFIG", str(tmp_path / "ocr.json"))
    assert configured() is None and load(None) is None
    (tmp_path / "ocr.json").write_text(json.dumps(dict(provider="none")), encoding="utf8")
    assert load(configured()) is None
    with pytest.raises(ValueError):
        OcrSettings(provider="rapidocr", unknown=1)
    # 모델 파일을 지정했는데 없으면 엔진을 만들기 전에 알린다(내려받기를 시도하지 않는다).
    with pytest.raises(ValueError, match="OCR_MODEL_FILE_MISSING: rec_model"):
        load(OcrSettings(provider="rapidocr", rec_model=str(tmp_path / "none.onnx")))


def test_installed_engine_reads_rendered_text():
    pytest.importorskip("rapidocr")
    pymupdf = pytest.importorskip("pymupdf")
    import numpy

    document = pymupdf.open()
    page = document.new_page(width=300, height=120)
    page.insert_text((30, 60), "1,234,567", fontsize=20)
    pix = page.get_pixmap(dpi=200)
    image = numpy.frombuffer(pix.samples, dtype=numpy.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
    result = read_image(load(OcrSettings(provider="rapidocr")), image, page_no=1, dpi=200)
    cells, loose, _ = assign(result.lines, [(0, 0, 300, 120)])
    assert same_text(cells[0]["text"], "1,234,567") and not loose and cells[0]["confidence"] > 0.8


def test_a_scanned_page_is_read_by_ocr_and_native_pages_are_left_alone(tmp_path, monkeypatch):
    pymupdf = pytest.importorskip("pymupdf")
    from doc_understanding.export.models import CleanDocument
    from nh_document_parser import parse_pdf

    monkeypatch.setenv("NH_OCR_CONFIG", str(tmp_path / "none.json"))  # 설정 파일 없음
    source = pymupdf.open()
    native = source.new_page(width=300, height=200)
    native.insert_text((30, 60), "native text", fontsize=14)
    scanned = source.new_page(width=300, height=200)
    stamp = pymupdf.open()
    stamp.new_page(width=300, height=200).insert_text((30, 60), "1,234", fontsize=20)
    scanned.insert_image(scanned.rect, pixmap=stamp[0].get_pixmap(dpi=100))  # 글자가 그림으로만 있는 쪽
    pdf = tmp_path / "mixed.pdf"
    source.save(pdf)

    without = parse_pdf(pdf)
    assert [page["text_source"] for page in without["pages"]] == ["native", "none"]

    engine = FakeEngine([([(100, 100), (300, 100), (300, 150), (100, 150)], "∆1,234", 0.91)])
    result = parse_pdf(pdf, evidence_dir=tmp_path / "evidence", ocr_engine=engine, ocr_dpi=200)
    CleanDocument.model_validate(result)
    first, second = result["pages"]
    assert first == without["pages"][0]  # 글자가 있는 쪽은 그대로다
    assert second["text_source"] == "ocr" and second["ocr"] == dict(engine="fake", model="fake-model", dpi=200, mean_confidence=0.91)
    line = second["items"][-1]
    # 읽은 글자 그대로, 좌표는 PDF 포인트(200dpi의 100화소 = 36pt), 신뢰도가 붙는다.
    assert (line["kind"], line["text"], line["bbox"], line["confidence"]) == ("paragraph", "∆1,234", [36.0, 36.0, 108.0, 54.0], 0.91)
    assert len(list((tmp_path / "evidence").rglob("ocr/0002.json"))) == 1


def test_ocr_lines_inside_a_ruled_table_become_cell_text():
    from types import SimpleNamespace as N

    from doc_understanding.export.clean import clean_document
    from doc_understanding.export.models import CleanDocument
    from doc_understanding.ocr import OcrPage

    def box(x0, y0, x1, y1):
        return N(x0=x0, y0=y0, x1=x1, y1=y1)

    cells = tuple(N(cell_id=f"d/p0001/physical000/cell{n:04d}", row_index=0, column_index=n, rowspan=1, colspan=1,
                    original_text="", source_bbox=box(100 * n, 100, 100 * n + 100, 150), status="resolved") for n in range(2))
    table = N(table_id="d/p0001/physical000", page_number=1, bounding_box=box(0, 100, 200, 150), rows=(1,), columns=(1, 2), cells=cells)
    page = N(page_number=1, text_status="ocr_required")
    doc = N(document_id="d", document_ir=N(pages=(page,)), table_structure=(), physical_tables=(table,), context_links=(),
            logical_table_links=(), validation_status="partial", sections=(), contents=(N(
                content_id="d/c1", kind="table", reading_order=1, source_references=(), table_id=table.table_id,
                context_content_ids=(), section_id=None),))
    read = OcrPage(page_no=1, engine="fake", model="m", dpi=200, mean_confidence=0.9, lines=[
        line("구분", 10, 110, 60, 130, 0.95), line("1,234", 120, 110, 180, 130, 0.88), line("표 아래 설명", 10, 180, 150, 195, 0.9),
        line("표 제목", 10, 70, 80, 90, 0.93)])
    clean = clean_document(doc, ocr={1: read})
    CleanDocument.model_validate(clean)
    grid = clean["tables"][0]["grid"]
    assert [(c["text"], c["confidence"]) for c in grid] == [("구분", 0.95), ("1,234", 0.88)]
    # 표 위에 인쇄된 줄은 표 앞, 아래 줄은 표 뒤에 온다. position은 그 순서로 다시 매긴다.
    assert [(i["position"], i.get("text", "table"), i.get("confidence")) for i in clean["pages"][0]["items"]] == [
        (1, "표 제목", 0.93), (2, "table", None), (3, "표 아래 설명", 0.9)]

    # 두 칸에 걸친 줄은 어느 칸에도 넣지 않고, 칸을 정하지 못했다고 적은 본문 항목으로 남긴다.
    across = OcrPage(page_no=1, engine="fake", model="m", dpi=200, mean_confidence=0.9, lines=[line("구분 금액", 20, 110, 180, 130)])
    clean = clean_document(doc, ocr={1: across})
    CleanDocument.model_validate(clean)
    assert [c["text"] for c in clean["tables"][0]["grid"]] == ["", ""]
    assert [(i.get("text"), i.get("cell_assignment")) for i in clean["pages"][0]["items"]] == [(None, None), ("구분 금액", "ambiguous")]


def test_the_configured_engine_is_not_created_for_a_pdf_whose_pages_all_have_text(tmp_path, monkeypatch):
    pymupdf = pytest.importorskip("pymupdf")
    from nh_document_parser import parse_pdf

    # 모델 파일이 없는 설정: 엔진을 만들면 바로 실패한다.
    config = tmp_path / "ocr.json"
    config.write_text(json.dumps(dict(provider="rapidocr", rec_model=str(tmp_path / "none.onnx"))), encoding="utf8")
    monkeypatch.setenv("NH_OCR_CONFIG", str(config))
    source = pymupdf.open()
    source.new_page(width=300, height=200).insert_text((30, 60), "native text", fontsize=14)
    source.save(tmp_path / "native.pdf")
    assert [page["text_source"] for page in parse_pdf(tmp_path / "native.pdf")["pages"]] == ["native"]

    stamp = pymupdf.open()
    stamp.new_page(width=300, height=200).insert_text((30, 60), "1,234", fontsize=20)
    page = source.new_page(width=300, height=200)
    page.insert_image(page.rect, pixmap=stamp[0].get_pixmap(dpi=100))
    source.save(tmp_path / "mixed.pdf")
    with pytest.raises(ValueError, match="OCR_MODEL_FILE_MISSING"):  # 읽을 쪽이 있으면 설정 문제를 숨기지 않는다
        parse_pdf(tmp_path / "mixed.pdf")
