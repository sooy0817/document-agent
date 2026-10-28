"""Synthetic geometry with test-only golden partitions. Production code never imports this."""

from dataclasses import dataclass

import pymupdf


@dataclass(frozen=True)
class GoldenCell:
    row: int
    column: int
    rowspan: int = 1
    colspan: int = 1
    label: str = ""


def partitions(kind="regular", rename=False):
    rows, cols = (6, 4) if kind == "nested" else (4, 4)
    merged = {
        "horizontal": [GoldenCell(0, 1, 1, 3, "Periods")],
        "vertical": [GoldenCell(0, 0, 3, 1, "Group")],
        "partial_horizontal": [GoldenCell(0, 0, 2, 1, "Group")],
        "nested": [
            GoldenCell(0, 0, 4, 1, "비유동"),
            GoldenCell(0, 1, 2, 1, "대출금"),
            GoldenCell(2, 1, 2, 1, "차입금"),
            GoldenCell(4, 0, 2, 1, "무수익성"),
            GoldenCell(4, 1, 2, 1, "Other group"),
        ],
    }.get(kind, [])
    covered = {
        (r, c)
        for cell in merged
        for r in range(cell.row, cell.row + cell.rowspan)
        for c in range(cell.column, cell.column + cell.colspan)
    }
    labels = (
        {(1, 2): "외상매출금", (3, 2): "일반차입금", (4, 2): "기타"} if kind == "nested" else {}
    )
    cells = merged + [
        GoldenCell(r, c, label=labels.get((r, c), "" if (r, c) == (3, 3) else f"r{r}c{c}"))
        for r in range(rows)
        for c in range(cols)
        if (r, c) not in covered
    ]
    if rename:
        cells = [
            GoldenCell(c.row, c.column, c.rowspan, c.colspan, f"X{i}") for i, c in enumerate(cells)
        ]
    return rows, cols, tuple(sorted(cells, key=lambda c: (c.row, c.column)))


def fixture_pdf(kind="regular", rename=False, scan=False):
    rows, cols, cells = partitions(kind, rename)
    doc = pymupdf.open()
    page = doc.new_page(width=900 if kind == "parallel" else 500, height=400)
    xs = [40, 140, 240, 340, 440]
    ys = [60 + 40 * i for i in range(rows + 1)]
    panels = [0, 420] if kind == "parallel" else [0]
    for shift in panels:
        edges = set()
        for cell in cells:
            r, c, rs, cs = cell.row, cell.column, cell.rowspan, cell.colspan
            for col in range(c, c + cs):
                edges.add(("h", r, col))
                edges.add(("h", r + rs, col))
            for row in range(r, r + rs):
                edges.add(("v", row, c))
                edges.add(("v", row, c + cs))
            if cell.label:
                page.insert_text(
                    (xs[c] + shift + 6, ys[r] + 22),
                    cell.label,
                    fontsize=9,
                    fontname="korea" if any(ord(ch) > 127 for ch in cell.label) else "helv",
                )
        if kind != "borderless":
            for axis, r, c in sorted(edges):
                if kind == "nonrectangular" and ((axis, r, c) in [("h", 1, 0), ("v", 0, 1)]):
                    continue
                if axis == "h":
                    if kind == "incomplete" and r == 2 and c == 1:
                        page.draw_line((xs[c] + shift, ys[r]), (xs[c] + shift + 35, ys[r]))
                        page.draw_line((xs[c + 1] + shift - 35, ys[r]), (xs[c + 1] + shift, ys[r]))
                    else:
                        page.draw_line((xs[c] + shift, ys[r]), (xs[c + 1] + shift, ys[r]))
                else:
                    page.draw_line((xs[c] + shift, ys[r]), (xs[c] + shift, ys[r + 1]))
    if scan:
        image = page.get_pixmap(dpi=120).tobytes("png")
        raster = pymupdf.open()
        p = raster.new_page(width=page.rect.width, height=page.rect.height)
        p.insert_image(p.rect, stream=image)
        data = raster.tobytes()
        raster.close()
    else:
        data = doc.tobytes()
    doc.close()
    return data, (rows, cols, cells)
