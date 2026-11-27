# 역할: 웹과 실험이 공유하는 원문 페이지 표시. 동결된 함수 본문을 그대로 유지한다.
"""Read-only page assembly from original spans and explicit nested links."""

from html import escape as esc


def bounds(t):
    return t["bounding_box"]


def page_view(doc, nested, number):
    page = next(p for p in doc["document_ir"]["pages"] if p["page_number"] == number)
    tables = {t["table_id"]: t for t in doc["physical_tables"] if t["page_number"] == number}
    native = {c["cell_id"]: c for t in page["tables"] for c in t["cells"]}
    spans = {s["span_id"]: s for b in page["blocks"] for s in b.get("spans", [])}
    children = {}
    owned = set()
    unresolved = []
    for link in nested["cells"]:
        if link["page_number"] != number:
            continue
        parent = native.get(link["parent_cell_id"])
        if not parent or link.get("containment_status") != "linked":
            continue
        box = parent["source"]["bbox"]
        candidates = [
            (t, c)
            for t in tables.values()
            for c in t["cells"]
            if max(abs(c["source_bbox"][k] - box[k]) for k in box) < 1.6
        ]
        if len(candidates) != 1:
            unresolved.append(link["parent_cell_id"])
            continue
        t, c = candidates[0]
        for tid in link["child_table_ids"]:
            if tid in tables and tid != t["table_id"]:
                children.setdefault(c["cell_id"], []).append(tid)
                owned.add(tid)

    def table_html(tid, ancestry=()):
        if tid in ancestry:
            return "<p>중첩 관계 검토 필요</p>"
        t = tables[tid]
        cells = t["cells"]
        rows = sorted({c["row_index"] for c in cells if c["row_index"] is not None})
        output = ['<table class="assembled-table" data-table="' + esc(tid) + '">']
        for row in rows:
            output.append("<tr>")
            for c in sorted(
                (c for c in cells if c["row_index"] == row),
                key=lambda c: c["column_index"] if c["column_index"] is not None else -1,
            ):
                kids = children.get(c["cell_id"], [])
                output.append(
                    f'<td rowspan="{c["rowspan"]}" colspan="{c["colspan"]}" data-source-cell="{esc(c["cell_id"])}">'
                )
                if kids:
                    consumed = {
                        sid for k in kids for cc in tables[k]["cells"] for sid in cc["span_refs"]
                    }
                    pieces = []
                    for sid in c["span_refs"]:
                        if sid in consumed or sid not in spans:
                            continue
                        s = spans[sid]
                        if s["text_raw"].strip():
                            pieces.append(
                                (
                                    s["source"]["bbox"]["y0"],
                                    s["source"]["bbox"]["x0"],
                                    "<span>" + esc(s["text_raw"]) + "</span>",
                                )
                            )
                    for k in kids:
                        pieces.append(
                            (
                                bounds(tables[k])["y0"],
                                bounds(tables[k])["x0"],
                                table_html(k, ancestry + (tid,)),
                            )
                        )
                    output.extend(x[2] for x in sorted(pieces, key=lambda x: (x[0], x[1])))
                else:
                    output.append(
                        '<button class="document-cell" data-cell="'
                        + esc(c["cell_id"])
                        + '" data-owner="'
                        + esc(tid)
                        + '">'
                        + esc(c["original_text"] or "")
                        + "</button>"
                    )
                output.append("</td>")
            output.append("</tr>")
        return "".join(output) + "</table>"

    consumed = {sid for t in tables.values() for c in t["cells"] for sid in c["span_refs"]}
    elements = []
    all_tables = {t["table_id"]: t for t in doc["physical_tables"]}
    for link in doc.get("logical_table_links", []):
        if link["target_table_id"] in tables:
            t = tables[link["target_table_id"]]
            source = all_tables[link["source_table_id"]]
            text = f"연결 후보: {source['page_number']}쪽 표의 제목·행 문맥 참조. 원본 표는 별도 보존하며 의미 검증 전입니다."
            elements.append(
                (bounds(t)["y0"] - 0.01, bounds(t)["x0"], f'<p class="hint">{esc(text)}</p>')
            )
    for tid, t in tables.items():
        if tid not in owned:
            elements.append((bounds(t)["y0"], bounds(t)["x0"], table_html(tid)))
    for b in page["blocks"]:
        remaining = [
            s for s in b.get("spans", []) if s["span_id"] not in consumed and s["text_raw"].strip()
        ]
        if not remaining:
            continue
        text = " ".join(s["text_raw"] for s in remaining)
        size = max(s.get("font_size", 10) for s in remaining)
        tag = "h2" if size >= 17 else "p"
        first = remaining[0]["source"]["bbox"]
        elements.append(
            (first["y0"], first["x0"], f'<{tag} class="source-text">' + esc(text) + f"</{tag}>")
        )
    return {
        "html": '<article class="assembled-document">'
        + "".join(x[2] for x in sorted(elements, key=lambda x: (x[0], x[1])))
        + "</article>",
        "top_level_tables": len(tables) - len(owned),
        "nested_tables": len(owned),
        "unresolved_parent_links": unresolved,
        "page": number,
        "notice": "원문 텍스트·병합·중첩을 페이지 순서로 재구성한 보기입니다. 원본의 서체·색상·로고·정확한 배치를 복제하지 않습니다.",
    }
