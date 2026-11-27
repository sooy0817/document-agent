"""Expand hand-authored IMAGE coordinates; never read predicted grids or extracted tables.

Coordinates below were marked by the agent on original 120 DPI page PNGs.
This is a reviewable annotation draft, not human-approved ground truth.
"""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/real-evaluation/d15c631c-0bd3-480d-9612-e5757ba85967"
OUT = ROOT / "tests/golden/ganggu-physical-v1.json"

# page, name, x/y pixel rulers, manually traced merged rectangles (r,c,rs,cs).
TABLES = [
    (
        8,
        "p08-t0",
        [79, 117, 350, 455, 561, 666, 773, 878],
        [
            226,
            255,
            284,
            315,
            345,
            376,
            406,
            437,
            467,
            497,
            528,
            558,
            589,
            619,
            650,
            680,
            709,
            738,
            767,
            797,
            826,
            855,
            884,
            914,
            943,
            972,
            1001,
            1030,
            1060,
            1089,
            1119,
            1149,
            1178,
            1208,
            1238,
        ],
        [
            (0, 0, 2, 2),
            (0, 2, 1, 2),
            (0, 4, 1, 2),
            (0, 6, 2, 1),
            (2, 0, 13, 1),
            (15, 0, 1, 2),
            (16, 0, 11, 1),
            (27, 0, 6, 1),
            (33, 0, 1, 2),
        ],
    ),
    (
        14,
        "p14-t0",
        [77, 184, 433, 542, 652, 762, 872],
        [205, 233, 279, 310, 340, 367, 394, 420, 446, 486, 529, 571],
        [
            (0, 0, 2, 2),
            (0, 2, 1, 3),
            (0, 5, 2, 1),
            (2, 0, 7, 1),
            (9, 0, 2, 1),
            (2, 1, 2, 1),
            (4, 1, 2, 1),
            (6, 1, 2, 1),
            (8, 2, 1, 3),
            (9, 2, 1, 3),
            (10, 2, 1, 3),
            (2, 5, 2, 1),
            (4, 5, 2, 1),
            (6, 5, 2, 1),
        ],
    ),
    (
        14,
        "p14-t1",
        [78, 234, 520, 637, 755, 876],
        [
            646,
            681,
            717,
            751,
            783,
            816,
            848,
            880,
            913,
            945,
            977,
            1009,
            1041,
            1074,
            1106,
            1138,
            1170,
            1202,
        ],
        [(3, 0, 6, 1), (9, 0, 8, 1)],
    ),
    (
        15,
        "p15-t0",
        [79, 118, 159, 195, 307, 424, 496, 613, 685, 789, 877],
        [
            164,
            197,
            229,
            262,
            298,
            333,
            369,
            405,
            440,
            472,
            505,
            538,
            570,
            603,
            636,
            669,
            702,
            737,
            772,
            807,
            842,
            878,
            913,
            946,
            979,
            1012,
            1047,
            1081,
            1115,
            1147,
            1180,
            1212,
        ],
        [
            (0, 0, 2, 4),
            (0, 4, 1, 2),
            (0, 6, 1, 2),
            (0, 8, 1, 2),
            (2, 0, 13, 1),
            (15, 0, 16, 1),
            (2, 1, 8, 1),
            (10, 1, 4, 1),
            (2, 2, 1, 2),
            (3, 2, 5, 1),
            (8, 2, 1, 2),
            (9, 2, 1, 2),
            (10, 2, 1, 2),
            (11, 2, 1, 2),
            (12, 2, 1, 2),
            (13, 2, 1, 2),
            (14, 1, 1, 3),
            (15, 1, 10, 1),
            (15, 2, 1, 2),
            (16, 2, 6, 1),
            (22, 2, 1, 2),
            (23, 2, 1, 2),
            (24, 2, 1, 2),
            (25, 1, 5, 1),
            (25, 2, 3, 1),
            (28, 2, 1, 2),
            (29, 2, 1, 2),
            (30, 1, 1, 3),
        ],
    ),
    (
        18,
        "p18-t0",
        [79, 190, 259, 328, 396, 465, 534, 603, 671, 740, 809, 878],
        [172, 215, 255, 298, 340, 382, 423],
        [(0, 0, 2, 1), (0, 1, 1, 4), (0, 5, 1, 4), (0, 9, 1, 2)],
    ),
    (
        18,
        "p18-t1",
        [79, 286, 407, 483, 604, 680, 802, 878],
        [571, 609, 647, 686, 725, 764, 802, 840, 879],
        [(0, 0, 2, 1), (0, 1, 1, 2), (0, 3, 1, 2), (0, 5, 1, 2)],
    ),
    (18, "p18-t2", [79, 375, 546, 709, 877], [978, 1020, 1067, 1114, 1160], []),
]


def expand(page, name, xp, yp, merges):
    xs, ys = [x * 0.6 for x in xp], [y * 0.6 for y in yp]
    used = set()
    cells = []
    for r, c, rs, cs in merges:
        slots = {(i, j) for i in range(r, r + rs) for j in range(c, c + cs)}
        assert not used & slots
        used |= slots
        cells.append((r, c, rs, cs))
    cells += [
        (r, c, 1, 1) for r in range(len(ys) - 1) for c in range(len(xs) - 1) if (r, c) not in used
    ]
    expanded = []
    edge_keys = set()
    for r, c, rs, cs in sorted(cells):
        expanded.append(
            dict(
                id=f"{name}-r{r}c{c}",
                row=r,
                column=c,
                rowspan=rs,
                colspan=cs,
                row_range=[r, r + rs],
                column_range=[c, c + cs],
                occupied_slots=[[i, j] for i in range(r, r + rs) for j in range(c, c + cs)],
                bbox=[xs[c], ys[r], xs[c + cs], ys[r + rs]],
            )
        )
        edge_keys.update(("horizontal", i, j) for i in (r, r + rs) for j in range(c, c + cs))
        edge_keys.update(("vertical", i, j) for i in range(r, r + rs) for j in (c, c + cs))
    edges = []
    for axis, r, c in sorted(edge_keys):
        outer = axis == "vertical" and c in (0, len(xs) - 1)
        edges.append(
            dict(
                axis=axis,
                row=r,
                column=c,
                position=ys[r] if axis == "horizontal" else xs[c],
                start=xs[c] if axis == "horizontal" else ys[r],
                end=xs[c + 1] if axis == "horizontal" else ys[r + 1],
                evidence="layout_extent_no_stroke" if outer else "visual_cell_separator",
                # Evaluate physical separator geometry, not dark-pixel recall.
                target="physical_cell_boundary",
            )
        )
    return dict(
        page=page,
        id=name,
        bbox=[xs[0], ys[0], xs[-1], ys[-1]],
        x_boundaries=xs,
        y_boundaries=ys,
        cells=expanded,
        boundaries=edges,
        annotation_status="agent_visual_reviewed_human_pending",
    )


def main():
    pages = [8, 14, 15, 18]
    result = dict(
        schema_version="manual-physical-golden/1.1",
        source_sha256=hashlib.sha256((SOURCE / "source.pdf").read_bytes()).hexdigest(),
        provenance=dict(
            author="Codex agent",
            method="manual visual tracing of original 120 DPI PNGs",
            authored_from="scripts/build_manual_golden.py literal pixel rulers and merges",
            prediction_imports=False,
            prior_prediction_exposure=True,
            review_status="agent_visual_reviewed; independent human review pending",
            revision_history=[
                "Initial source-image tracing at 120 DPI; predictions never imported.",
                "Source overlay rereview: p8 left group borders include liability/capital totals.",
                "220 DPI source crops: p14 right blank column has individual row rules; p15 left outer group includes bottom subtotal. Corrected manual draft, not algorithm output. Initial draft retained in reports/golden-initial-draft.json.",
            ],
            detail_review_images=[
                "data/quality-golden/p14-right-detail.png",
                "data/quality-golden/p15-bottom-detail.png",
            ],
            limitation="Development annotations, not blinded holdout or expert-certified ground truth",
            coordinate_tolerance_points=1.5,
            row_ranges="0-based half-open",
            source_image_sha256={
                str(p): hashlib.sha256((SOURCE / f"pages/{p:04d}.png").read_bytes()).hexdigest()
                for p in pages
            },
        ),
        complete_detection_pages=pages,
        tables=[expand(*t) for t in TABLES],
        negative_regions=[
            dict(
                page=3,
                bbox=[40, 50, 540, 760],
                kind="table_of_contents",
                review="source image reviewed",
            ),
            dict(
                page=6,
                bbox=[70, 230, 500, 470],
                kind="organization_chart",
                review="source image reviewed",
            ),
            dict(
                page=21,
                bbox=[45, 600, 530, 710],
                kind="organization_chart",
                review="source image reviewed",
            ),
        ],
        focused_relations=[
            dict(
                table="p15-t0",
                container=[3, 2],
                member=[7, 3],
                expected="row_range_contains",
                label="차입금 / 일반차입금",
            ),
            dict(
                table="p15-t0",
                container=[16, 2],
                member=[21, 3],
                expected="row_range_contains",
                label="대출금 / 외상매출금",
            ),
            dict(
                table="p15-t0",
                container=[25, 2],
                member=[28, 2],
                expected="row_range_disjoint",
                label="비유동 / 기타",
            ),
        ],
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    # Rendering consumes only the manual annotation and original PDF, never predictions.
    import pymupdf

    image_folder = ROOT / "data/quality-golden"
    image_folder.mkdir(exist_ok=True)
    with pymupdf.open(SOURCE / "source.pdf") as document:
        for number in pages:
            page = document[number - 1]
            page.set_rotation(0)
            shape = page.new_shape()
            for table in result["tables"]:
                if table["page"] != number:
                    continue
                for cell in table["cells"]:
                    shape.draw_rect(pymupdf.Rect(cell["bbox"]))
                    shape.finish(
                        color=(1, 0, 0)
                        if cell["rowspan"] > 1 or cell["colspan"] > 1
                        else (0, 0.6, 0),
                        width=0.6,
                    )
            shape.commit()
            page.get_pixmap(dpi=120).save(image_folder / f"golden-{number}.png")
    print(OUT)


if __name__ == "__main__":
    main()
