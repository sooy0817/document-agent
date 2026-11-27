"""한글 합성 금리표 PDF를 만들고, 수정하지 않은 파서로 읽어 결과를 저장한다.

기대 정답은 이 파일 어디에도 파서 입력으로 들어가지 않는다. 결과는 파서가 낸 그대로이며
맞는지 여부를 여기서 판정하지 않는다. 수치와 상품은 모두 가상이다.

실행(저장소 루트, `pip install .` 이후):
    python examples/rate_table_ko.py
결과: examples/rate-table-ko.pdf, rate-table-ko.json(tabledoc), rate-table-ko.txt
필요: 기본 의존성만. 모델·DB·OCR 호출 없음.
"""
from __future__ import annotations

import json
from itertools import groupby
from pathlib import Path

import pymupdf

from nh_document_parser import parse_pdf, render_text

ROOT = Path(__file__).resolve().parent
PDF = ROOT / "rate-table-ko.pdf"
INK, RULE = (0.09, 0.19, 0.27), (0.19, 0.29, 0.36)


def author() -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)

    def text(x, y, value, size=10):
        # 한글은 내장 CJK 글꼴, 숫자·기호는 Helvetica로 나눠 쓴다(글꼴 파일을 넣지 않아 PDF가 작다).
        for is_ascii, run in groupby(value, key=str.isascii):
            part = "".join(run)
            font = "helv" if is_ascii else "korea"
            page.insert_text((x, y), part, fontname=font, fontsize=size, color=INK)
            x += pymupdf.get_text_length(part, fontname=font, fontsize=size)

    def line(x0, y0, x1, y1):
        page.draw_line((x0, y0), (x1, y1), color=RULE, width=0.65)

    def fill(x, y, w, h, color):
        page.draw_rect(pymupdf.Rect(x, y, x + w, y + h), color=None, fill=color)

    text(42, 81, "가상 예금 금리 안내", 21)
    text(42, 106, "합성 예시입니다. 실제 금융기관·상품·금리가 아닙니다.")
    text(42, 170, "단위: 연 %, 세전. 기준: 각 연도 말.", 9)
    # 라벨 3열(구분 › 상품 › 가입기간), 머리글 3행(연도 › 고객 › 금리 종류), 값 8열.
    top, head, row = 193, 24, 30
    body = top + 3 * head
    bottom = body + 8 * row
    fill(42, top, 528, 3 * head, (0.91, 0.94, 0.96))
    fill(42, body, 160, 8 * row, (0.95, 0.96, 0.96))
    line(42, top, 570, top)
    line(202, top + head, 570, top + head)
    line(202, top + 2 * head, 570, top + 2 * head)
    for x in (42, 202, 386, 570):                      # 모서리, 연도 경계
        line(x, top, x, bottom)
    for x in (294, 478):                               # 고객 경계
        line(x, top + head, x, bottom)
    for x in (248, 340, 432, 524):                     # 금리 종류 경계
        line(x, top + 2 * head, x, bottom)
    line(92, body, 92, bottom)
    line(152, body, 152, bottom)
    groups = (("예금", 0, 5), ("적금", 5, 8))
    products = (("정기예금", 0, 3), ("자유적립예금", 3, 5), ("정기적금", 5, 7), ("청년적금", 7, 8))
    for k in range(9):
        y = body + k * row
        left = 42 if k in (0, 5, 8) else 92 if k in (3, 7) else 152
        line(left, y, 570, y)
    text(48, top + 40, "구분")
    text(276, top + 16, "2024년")
    text(460, top + 16, "2025년")
    for x, label in ((236, "개인"), (328, "법인"), (420, "개인"), (512, "법인")):
        text(x, top + head + 16, label)
    for k in range(8):
        text(208 + 46 * k + 6, top + 2 * head + 16, "기본" if k % 2 == 0 else "우대", 9)
    for label, a, b in groups:
        text(48, body + (a + b) * row / 2 + 4, label)
    for label, a, b in products:
        text(96, body + (a + b) * row / 2 + 4, label, 8.5)
    rows = (("6개월", "2.10", "0.20", "1.90", "", "2.40", "0.30", "2.20", ""),
            ("12개월", "2.50", "0.30", "2.30", "0.10", "2.80", "0.40", "2.60", "0.10"),
            ("24개월", "2.70", "0.30", "2.50", "0.10", "3.00", "0.40", "2.80", "0.20"),
            ("12개월", "2.30", "", "2.10", "", "2.60", "0.00", "2.40", ""),
            ("24개월", "2.50", "", "2.30", "", "2.80", "0.10", "2.60", ""),
            ("12개월", "2.70", "0.20", "", "", "3.00", "0.30", "", ""),
            ("24개월", "3.00", "0.30", "", "", "3.20", "0.40", "", ""),
            ("24개월", "3.50", "0.50", "", "", "3.80", "0.70", "", ""))
    for k, (period, *values) in enumerate(rows):
        y = body + k * row + 19
        text(158, y, period, 9)
        for c, value in enumerate(values):
            if value:
                text(208 + 46 * c + 8, y, value, 9)
    text(42, bottom + 26, "빈칸은 해당 없음(가입 대상이 아니거나 우대금리 없음), 0.00은 우대금리가 0이라는 표기입니다.", 9)
    text(42, 752, "가상 자료. 파서 결과는 후보이며 승인된 금융 정보가 아닙니다.", 9)
    doc.save(PDF, garbage=4, deflate=True, no_new_id=True)


def extract() -> None:
    document = parse_pdf(PDF)
    PDF.with_suffix(".json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    PDF.with_suffix(".txt").write_text(render_text(document), encoding="utf-8")
    table = max(document["tables"], key=lambda t: len(t["grid"]))
    print(json.dumps(dict(format=document["format"], tables=len(document["tables"]),
                          cells=len(table["grid"]), value_paths=len(table["paths"]),
                          table_state=table["state"],
                          validation_status=document["document"]["validation_status"],
                          facts_published=document["document"]["facts_published"]),
                     ensure_ascii=False))


if __name__ == "__main__":
    author()
    extract()
