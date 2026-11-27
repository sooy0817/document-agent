from doc_understanding.tools import relation_index


def packet():
    def cell(s, text, b):
        return dict(
            id=s,
            kind="cell",
            text=text,
            table_id="T",
            page=1,
            bbox=dict(zip(("x0", "y0", "x1", "y1"), b)),
        )

    return dict(
        target_ids=["V"],
        unreviewed_target_ids=[],
        omitted_ids=[],
        evidence=[
            cell("R", "항목", (0, 20, 20, 40)),
            cell("C", "설명", (20, 0, 60, 20)),
            cell("V", "조건 없음", (20, 20, 60, 40)),
        ],
    )


def relation(kind="row_header", sid="R"):
    return dict(
        relation=kind,
        source_id=sid,
        target_id="V",
        citations=[
            dict(source_id=sid, quote="항목" if sid == "R" else "설명"),
            dict(source_id="V", quote="조건 없음"),
        ],
    )


def outputs():
    row = dict(
        status="explicit",
        header_ids=["R"],
        reason="원본 행 항목",
        citations=relation()["citations"],
    )
    col = dict(
        status="explicit",
        header_ids=["C"],
        reason="원본 열 항목",
        citations=relation("column_header", "C")["citations"],
    )
    i = dict(
        summary="후보",
        targets=[dict(cell_id="V", role="value", row_axis=row, column_axis=col, context_ids=[])],
        missing_evidence=[],
    )
    j = dict(
        summary="검수",
        decisions=[
            dict(
                cell_id="V",
                axis=a,
                verdict="supported",
                reason="원본 근거",
                citations=i["targets"][0][a + "_axis"]["citations"],
            )
            for a in ("row", "column")
        ],
        missing_evidence=[],
    )
    return i, j


def empty_index():
    return relation_index.RelationIndex(dict(physical_tables=[], table_structure=[]), {"cells": []})
