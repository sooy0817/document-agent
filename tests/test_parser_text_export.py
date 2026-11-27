import sys

import pytest

from nh_document_parser import cli, render_text


def cell(column, text, role, row=0, span=1):
    return dict(col=column, row=row, text=text, role=role, rowspan=span, colspan=1)


def document(rows):
    return dict(tables=[dict(table_id=1, page_no=1, columns=2, panels=[dict(header_rows=1)],
                             grid=rows)], pages=[dict(page_no=1, items=[dict(kind="table", table_id=1)])])


def test_readable_rows_keep_merged_label_and_blank_value():
    doc = document([cell(0, "Group", "corner"), cell(1, "Count", "column_header"),
                    cell(0, "East", "row_label", 1, 2), cell(1, "0", "value", 1),
                    cell(1, "", "value", 2)])
    assert render_text(doc).splitlines()[-2:] == ["Group: East / Count: 0", "Group: East / Count: [빈칸]"]


def test_numeric_first_row_of_continued_table_stays_raw():
    doc = document([cell(0, "GBP", "corner"), cell(1, "4.0712", "column_header"),
                    cell(0, "CAD", "row_label", 1), cell(1, "4.2215", "value", 1)])
    assert render_text(doc).splitlines()[-2:] == ["GBP / 4.0712", "CAD / 4.2215"]


def test_dual_export_rolls_back_new_json_if_text_cannot_be_created(tmp_path, monkeypatch):
    output = tmp_path / "result.json"
    monkeypatch.setattr(cli, "parse_pdf", lambda *args, **kwargs: document([]))
    monkeypatch.setattr(sys, "argv", ["nh-parse", "input.pdf", "-o", str(output), "--text-output",
                                     str(tmp_path / "missing" / "result.txt")])
    with pytest.raises(FileNotFoundError):
        cli.main()
    assert not output.exists()
