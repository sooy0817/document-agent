"""설치된 파서의 CLI와 Python API로 본문·표가 섞인 합성 PDF를 검사한다."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sysconfig
from tempfile import TemporaryDirectory

import pymupdf

import nh_document_parser


def sample_pdf(path):
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((48, 60), "Application procedures")
        rows = [
            ("Group", "Instructions", "Minimum", "Maximum"),
            ("Member", "Return the signed form.", "10", "20"),
            ("Guest", "Visit the office.", "30", "40"),
            ("Other", "Use the help desk.", "50", "60"),
        ]
        columns = (48, 130, 290, 380, 475)
        for i in range(len(rows) + 1):
            y = 100 + i * 30
            page.draw_line((columns[0], y), (columns[-1], y))
        for x in columns:
            page.draw_line((x, 100), (x, 220))
        for row, cells in enumerate(rows):
            for x, cell in zip(columns, cells):
                page.insert_text((x + 4, 120 + row * 30), cell, fontsize=10)
        page.insert_text((48, 265), "Exceptions apply on holidays.")
        pdf.save(path)


class Engine:
    """주입한 엔진. 실제 OCR 엔진 없이 설치본의 OCR 경로를 지난다."""
    name, model = "check", "check-model"

    def read(self, image):
        return [([(130, 150), (500, 150), (500, 175), (130, 175)], "Application procedures", 0.9)]


def check():
    library = Path(sysconfig.get_path("purelib")).resolve()
    assert Path(nh_document_parser.__file__).resolve().is_relative_to(library)
    for absent in ("doc_understanding.rag", "doc_understanding.web", "langgraph", "psycopg"):
        assert importlib.util.find_spec(absent) is None, absent
    command = Path(sysconfig.get_path("scripts")) / ("nh-parse.exe" if os.name == "nt" else "nh-parse")
    with TemporaryDirectory() as directory:
        root = Path(directory)
        pdf = root / "example.pdf"
        sample_pdf(pdf)
        output = root / "document.json"
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        # OCR을 켠 설정인데 모델 파일이 없는 설치: 글자가 있는 PDF는 엔진을 만들지 않고 그대로 분석돼야 한다.
        config = root / "ocr.json"
        config.write_text(json.dumps(dict(provider="rapidocr", rec_model=str(root / "absent.onnx"))), encoding="utf-8")
        env["NH_OCR_CONFIG"] = os.environ["NH_OCR_CONFIG"] = str(config)
        text_file = root / "document.txt"
        subprocess.run([command, pdf, "-o", output, "--text-output", text_file], cwd=root, env=env, check=True)
        document = json.loads(output.read_text(encoding="utf-8"))
        text = text_file.read_text(encoding="utf8")
        assert text == nh_document_parser.render_text(document)
        assert "Group: Member / Instructions: Return the signed form. / Minimum: 10" in text
        assert text.index("Application procedures") < text.index("[표 1]") < text.index("Exceptions apply on holidays.")
        assert document["format"] == "tabledoc/0.5"
        assert document["document"]["document_id"].endswith(hashlib.sha256(pdf.read_bytes()).hexdigest())
        page, = document["pages"]
        table, = document["tables"]
        items = [i for i in page["items"] if not i.get("inside_tables")]
        assert [i.get("text", i.get("table_id")) for i in items] == [
            "Application procedures", table["table_id"], "Exceptions apply on holidays."
        ]
        cells = {c["text"]: c["cell_id"] for c in table["grid"]}
        paths = {p["cell_id"]: p for p in table["paths"]}
        row_labels = {n["node_id"]: n["label_raw"] for n in table["row_nodes"]}
        col_labels = {n["node_id"]: n["label_raw"] for n in table["column_nodes"]}
        for value, column in (("Return the signed form.", "Instructions"), ("10", "Minimum")):
            path = paths[cells[value]]
            assert row_labels[path["row_node"]] == "Member"
            assert col_labels[path["column_node"]] == column
        evidence = root / "runs"
        result = nh_document_parser.parse_pdf(pdf, evidence_dir=evidence)
        assert result == document
        assert len(list(evidence.glob("*/document-ir.json"))) == 1
        scanned = root / "scanned.pdf"
        with pymupdf.open(pdf) as source, pymupdf.open() as image_only:
            page = image_only.new_page()
            page.insert_image(page.rect, pixmap=source[0].get_pixmap(dpi=100))  # 글자가 그림으로만 있는 쪽
            image_only.save(scanned)
        try:
            nh_document_parser.parse_pdf(scanned)
        except ValueError as error:  # 읽을 쪽이 있으면 잘못된 설정을 알린다
            assert "OCR_MODEL_FILE_MISSING" in str(error)
        else:
            raise AssertionError("a scanned page was parsed with an unusable OCR configuration")
        read = nh_document_parser.parse_pdf(scanned, ocr_engine=Engine(), ocr_dpi=200)
        page, = read["pages"]
        assert page["text_source"] == "ocr" and page["ocr"]["engine"] == "check"
        assert [(i["text"], i["confidence"]) for i in page["items"] if i.get("confidence")] == [("Application procedures", 0.9)]
        assert json.loads((Path(nh_document_parser.__file__).parent / "clean-document.schema.json").read_text(
            encoding="utf-8"
        ))["title"] == "CleanDocument"
    print("Installed parser passed: ordered text and table, sentence and numeric cell paths, CLI and API, "
          "native PDF without the configured OCR engine, scanned page through a supplied engine.")


if __name__ == "__main__":
    check()
