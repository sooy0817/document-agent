"""Non-financial synthetic layouts. Expected edges authored before any analyzer run.

No real-PDF cell is copied here; labels have no role in layout construction.
"""

from uuid import UUID
from doc_understanding.domain.models import BBox, SourceRef
from doc_understanding.table_understanding.physical_models import (
    GridBand,
    GridPolicy,
    PhysicalCell,
    PhysicalTableIR,
)


def compound(kind="laboratory", rename=False):
    # Three independent layouts: laboratory hierarchy, nested warehouse groups,
    # and multilevel survey columns with repeated opaque labels.
    if kind == "laboratory":
        rows, cols = 6, 6
        spans = [(0, 0, 2, 2), (0, 2, 1, 2), (0, 4, 1, 2), (2, 0, 4, 1)]
        expected_rows = [((r, 1), (2, 0)) for r in range(2, 6)]
        expected_columns = [((1, c), (0, 2 if c < 4 else 4)) for c in range(2, 6)]
    elif kind == "warehouse":
        rows, cols = 8, 7
        spans = [
            (0, 0, 2, 3),
            (0, 3, 1, 2),
            (0, 5, 1, 2),
            (2, 0, 6, 1),
            (2, 1, 3, 1),
            (5, 1, 2, 1),
            (7, 1, 1, 2),
        ]
        expected_rows = [((2, 1), (2, 0)), ((5, 1), (2, 0)), ((7, 1), (2, 0))]
        expected_rows += [((r, 2), (2, 1) if r < 5 else (5, 1)) for r in range(2, 7)]
        expected_columns = [((1, c), (0, 3 if c < 5 else 5)) for c in range(3, 7)]
    elif kind == "survey":
        rows, cols = 6, 5
        spans = [(0, 0, 3, 1), (0, 1, 1, 4), (1, 1, 1, 2), (1, 3, 1, 2)]
        expected_rows = []
        expected_columns = [((1, 1), (0, 1)), ((1, 3), (0, 1))]
        expected_columns += [((2, c), (1, 1 if c < 3 else 3)) for c in range(1, 5)]
    else:
        raise ValueError(kind)
    occupied = {
        (rr, cc) for r, c, rs, cs in spans for rr in range(r, r + rs) for cc in range(c, c + cs)
    }
    spans += [(r, c, 1, 1) for r in range(rows) for c in range(cols) if (r, c) not in occupied]
    cells = []
    for r, c, rs, cs in sorted(spans):
        box = BBox(x0=20 + c * 50, y0=20 + r * 24, x1=20 + (c + cs) * 50, y1=20 + (r + rs) * 24)
        cid = f"r{r}c{c}"
        label = f"arbitrary-{r}-{c}-ζ" if rename else ("Repeat" if c % 2 else f"{kind}-{r}")
        cells.append(
            PhysicalCell(
                cell_id=cid,
                row_index=r,
                column_index=c,
                rowspan=rs,
                colspan=cs,
                original_text=label,
                source_bbox=box,
                source=SourceRef(document_id=kind, page_number=1, bbox=box, cell_id=cid),
                extraction_method="vector_grid",
                status="resolved",
                occupied_slots=tuple(
                    (rr, cc) for rr in range(r, r + rs) for cc in range(c, c + cs)
                ),
            )
        )
    box = BBox(x0=20, y0=20, x1=20 + cols * 50, y1=20 + rows * 24)
    table = PhysicalTableIR(
        table_id=kind,
        document_id=kind,
        analysis_run_id=UUID(int=1),
        policy=GridPolicy(),
        source_ir_sha256="0" * 64,
        page_number=1,
        bounding_box=box,
        rows=tuple(GridBand(index=i, start=20 + i * 24, end=44 + i * 24) for i in range(rows)),
        columns=tuple(GridBand(index=i, start=20 + i * 50, end=70 + i * 50) for i in range(cols)),
        cells=tuple(cells),
        source_cells=(),
        source_links=(),
        page_refs=(1,),
        source_references=(SourceRef(document_id=kind, page_number=1, bbox=box),),
        lines=(),
        intersections=(),
        boundaries=(),
        unresolved_boundaries=(),
    )

    def ids(edges):
        return {(f"r{a[0]}c{a[1]}", f"r{b[0]}c{b[1]}") for a, b in edges}

    return table, ids(expected_rows), ids(expected_columns)


def fixture_pdf(kind):
    import pymupdf

    table = compound(kind)[0]
    with pymupdf.open() as doc:
        page = doc.new_page(width=440, height=320)
        for c in table.cells:
            rect = pymupdf.Rect(c.source_bbox.xyxy)
            page.draw_rect(rect, color=(0, 0, 0), width=0.6)
            page.insert_text((rect.x0 + 3, rect.y0 + 12), c.original_text, fontsize=5)
        return doc.tobytes()
