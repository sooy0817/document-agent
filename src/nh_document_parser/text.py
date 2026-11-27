"""Render the physical rows of a tabledoc document for reading and copying."""
from collections import defaultdict


def _cell_text(cell):
    return " ".join((cell.get("text") or "").split())


def _table_lines(table):
    yield f"[표 {table['table_id']}]"
    rows = defaultdict(list)
    unplaced = []
    for cell in table["grid"]:
        if cell.get("row") is None or cell.get("col") is None:
            unplaced.append(cell)
        else:
            rows[cell["row"]].append(cell)
    for cells in rows.values():
        cells.sort(key=lambda c: c["col"])

    # Pair headings with values only for a simple, complete header row.
    # Continued tables can misclassify an entire row of rates as column headings.
    first = rows.get(0, [])
    has_labels = (
        len(rows) > 1 and len(table.get("panels", [])) == 1
        and table["panels"][0]["header_rows"] == 1
        and len(first) == table["columns"]
        and all(c["col"] == i and c.get("rowspan") == c.get("colspan") == 1
                and c["role"] in ("corner", "column_header")
                and any(ch.isalpha() for ch in _cell_text(c)) for i, c in enumerate(first))
    )
    carry = []
    for row, cells in sorted(rows.items()):
        if has_labels and row == 0:
            yield " / ".join(map(_cell_text, cells))
            continue
        carry = [c for c in carry if row < c["row"] + c["rowspan"]]
        displayed = sorted(carry + cells, key=lambda c: c["col"])
        fields = []
        for cell in displayed:
            value = _cell_text(cell) or "[빈칸]"
            if has_labels and cell.get("colspan") == 1 and 0 <= cell["col"] < len(first):
                value = f"{_cell_text(first[cell['col']])}: {value}"
            fields.append(value)
        yield " / ".join(fields)
        if has_labels:
            carry.extend(c for c in cells if (c.get("rowspan") or 1) > 1)
    for cell in unplaced:
        yield f"[위치 미확정 셀 {cell['cell_id']}] {_cell_text(cell) or '[빈칸]'}"


def render_text(document: dict) -> str:
    """Return page text and physical table rows in the published reading order."""
    tables = {t["table_id"]: t for t in document["tables"]}
    by_page = defaultdict(list)
    for table in tables.values():
        by_page[table["page_no"]].append(table)
    lines = []
    shown = set()
    for page in document["pages"]:
        lines.append(f"[{page['page_no']}쪽]")
        for item in page["items"]:
            if item["kind"] == "table":
                tid = item["table_id"]
                if tid not in shown:
                    lines.extend(_table_lines(tables[tid]))
                    shown.add(tid)
            elif not item.get("inside_tables") and item.get("text"):
                prefix = "[셀 배정 미확정] " if item.get("cell_assignment") == "ambiguous" else ""
                lines.append(prefix + item["text"])
        for table in by_page[page["page_no"]]:
            if table["table_id"] not in shown:
                lines.extend(_table_lines(table))
                shown.add(table["table_id"])
        lines.append("")
    return "\n".join(lines)
