# 역할: 문서 기록(CanonicalDocument)을 사람이 읽을 수 있는 정리된 JSON으로 펼친다. 원본 기록은 고치지 않는다.
"""정리된 문서 출력(ADR-080의 "펼쳐 보기").

    python -m doc_understanding.export.clean <document.json> <out.json>

큰 문서 기록에서 질문·검토에 쓰는 것만 한 번씩 담는다: 쪽마다 읽는 순서의 항목(글과 표), 표의 격자·머리글 트리·
값의 경로, 글과 표의 연결, 섹션(제목 아래 묶음)과 그 소속. 도형·경계선·내부 해시는 넣지 않는다. 식별자는 문서 안의 짧은 번호로 바꾸고,
원래 식별자는 `ref`에 한 번만 둔다. 머리글 라벨은 원문(`label_raw`)과 붙여 쓴 것(`label`)을 함께 낸다.
규칙이 추론한 상태(observed·inferred·unresolved)는 그대로 옮긴다. 이 출력은 사람이 확인한 사실이 아니다.
"""
import json
import re
import sys
from pathlib import Path

from doc_understanding.export.models import FORMAT, CleanDocument
from doc_understanding.ocr.assign import assign
from doc_understanding.export.parent_cells import attach_parent_cells

TEXT_SOURCE = dict(native="native", ocr_required="none", empty="none")


def label_text(raw):
    """세로쓰기처럼 한 글자씩 줄이 바뀐 라벨은 붙이고, 그 밖의 줄바꿈은 공백으로 바꾼다. 원문은 따로 보존한다."""
    if raw is None:
        return None
    lines = [line.strip() for line in raw.split("\n") if line.strip()]
    if len(lines) > 1 and all(len(line) == 1 for line in lines):
        return "".join(lines)
    return re.sub(r"\s+", " ", " ".join(lines))


def box(bbox):
    return [round(v, 1) for v in (bbox.x0, bbox.y0, bbox.x1, bbox.y1)]


def clean_table(number, physical, structured):
    """물리 표 하나와 그 구조 후보를 격자·머리글 트리·경로로 펼친다."""
    cells = {c.cell_id: n for n, c in enumerate(physical.cells, 1)}
    table = dict(table_id=number, ref=physical.table_id.split("/", 1)[-1], page_no=physical.page_number,
                 bbox=box(physical.bounding_box), rows=len(physical.rows), columns=len(physical.columns))
    roles, nodes, node_cell = {}, {}, {}
    if structured is not None and hasattr(structured, "panels"):
        table["state"] = structured.analysis_state
        panels = {p.panel_id: n for n, p in enumerate(structured.panels)}
        table["panels"] = [dict(panel_no=panels[p.panel_id], first_col=p.column_range[0], last_col=p.column_range[1],
                                header_rows=p.header_rows, label_columns=p.stub_columns) for p in structured.panels]
        roles = {r.cell_id: r.role for r in structured.cell_roles}
        for axis, tree in (("row", structured.row_tree), ("column", structured.column_tree)):
            for node in tree:
                nodes[node.node_id] = len(nodes) + 1
            table[axis + "_nodes"] = [
                dict(node_id=nodes[n.node_id], parent=nodes.get(n.parent_id), panel_no=panels.get(n.panel_id, 0),
                     label_raw=n.label_raw, label=label_text(n.label_raw), cell_id=cells.get(n.source_cell_id),
                     state=n.evidence_state) for n in tree]
            node_cell.update({n.source_cell_id: nodes[n.node_id] for n in tree})
        table["paths"] = [
            dict(cell_id=cells[m.cell_id], row_node=nodes.get(m.row_path[-1]) if m.row_path else None,
                 column_node=nodes.get(m.column_path[-1]) if m.column_path else None, state=m.evidence_state)
            for m in structured.cell_mappings if m.cell_id in cells]
        table["open_questions"] = [dict(code=a.code, reason=a.reason) for a in structured.ambiguities]
    else:
        table["state"] = "not_analyzed"
    grid = []
    for cell in physical.cells:
        item = dict(cell_id=cells[cell.cell_id], row=cell.row_index, col=cell.column_index, rowspan=cell.rowspan,
                    colspan=cell.colspan, role=roles.get(cell.cell_id, "unknown"), text=cell.original_text,
                    bbox=box(cell.source_bbox))
        if cell.status != "resolved":
            item["status"] = cell.status
        if cell.cell_id in node_cell:
            item["node"] = node_cell[cell.cell_id]
        grid.append(item)
    table["grid"] = grid
    return table, nodes


def clean_document(doc, ocr=None):
    """CanonicalDocument → 정리된 dict. 입력은 바꾸지 않는다.

    ocr: {쪽 번호: OcrPage}. 글자가 없던 쪽에서 그림으로 읽은 줄이다. 주면 그 쪽의 `text_source`가 `ocr`이 되고,
    선으로 나뉜 표의 칸에 든 줄은 그 칸의 글자와 신뢰도가 되고, 나머지 줄은 인쇄된 높이의 순서로 본문 항목이 된다.
    두 칸에 걸치거나 경계에 놓여 칸을 정할 수 없는 줄은 칸에 넣지 않고 `cell_assignment: "ambiguous"` 본문 항목으로 둔다.
    그림으로 읽은 표의 행·열 관계는 글자 없이 분석된 것이므로 확정된 것으로 쓰지 않는다.
    """
    ir = doc.document_ir
    structured = {t.table_id: t for t in doc.table_structure}
    tables = {t.table_id: n for n, t in enumerate(doc.physical_tables, 1)}
    contents = {c.content_id: n for n, c in enumerate(doc.contents, 1)}
    ordered = sorted(doc.sections, key=lambda s: s.reading_order)
    sections = {s.section_id: n for n, s in enumerate(ordered, 1)}
    section_pages = {}
    pages ={p.page_number: dict(page_no=p.page_number, text_source=TEXT_SOURCE.get(getattr(p, "text_status", None)),
                                 items=[]) for p in (ir.pages if ir else ())}
    by_ref = {}
    for content in sorted(doc.contents, key=lambda c: c.reading_order):
        page_no = (content.source_references[0].page_number if content.source_references
                   else next((t.page_number for t in doc.physical_tables if t.table_id == content.table_id), None))
        page = pages.setdefault(page_no, dict(page_no=page_no, items=[]))
        if content.kind == "table":
            item = dict(position=len(page["items"]) + 1, kind="table", table_id=tables.get(content.table_id))
            if content.context_content_ids:  # 표를 설명한다고 본 본문 줄(제목·단위 등)
                item["context"] = [contents[c] for c in content.context_content_ids if c in contents]
        else:
            item = dict(position=len(page["items"]) + 1, kind=content.kind, content_id=contents[content.content_id],
                        text=content.text_raw)
            if content.content_id + "/section" in sections:  # 이 줄이 섹션의 제목이다
                item["section_title"] = True
            if content.duplicate_of_table_ids:  # 표 안의 글자를 다시 뽑은 줄. 본문으로 읽지 않는다.
                item["inside_tables"] = [tables[t] for t in content.duplicate_of_table_ids if t in tables]
            if content.source_references:
                item["bbox"] = box(content.source_references[0].bbox)
                first = content.source_references[0]
                by_ref[(first.page_number, first.block_id, first.span_id)] = contents[content.content_id]
        if content.section_id in sections:  # 소속을 정하지 못한 항목에는 넣지 않는다
            item["section_id"] = sections[content.section_id]
            if page_no is not None:
                section_pages.setdefault(item["section_id"], []).append(page_no)
        page["items"].append(item)
    outline = [dict(section_id=sections[s.section_id], parent=sections.get(s.parent_id), title=s.title_raw,
                    title_content_id=contents.get(s.section_id.removesuffix("/section")),
                    first_page=min(section_pages.get(sections[s.section_id], [None]), default=None),
                    last_page=max(section_pages.get(sections[s.section_id], [None]), default=None),
                    reprinted_title_content_ids=[by_ref[k] for k in ((r.page_number, r.block_id, r.span_id)
                                                                     for r in s.source_references[1:]) if k in by_ref],
                    state=s.evidence_state) for s in ordered]
    cleaned, node_ref = [], {}
    for physical in doc.physical_tables:
        table, nodes = clean_table(tables[physical.table_id], physical, structured.get(physical.table_id))
        cleaned.append(table)
        node_ref.update({node: (table["table_id"], number) for node, number in nodes.items()})
    attach_parent_cells(doc, cleaned)
    numbered = len(contents)
    for page_no, read in sorted((ocr or {}).items()):
        page = pages.setdefault(page_no, dict(page_no=page_no, items=[]))
        if page.get("text_source") not in (None, "none"):
            continue  # 글자가 있는 쪽은 그대로 둔다
        page.update(text_source="ocr", ocr=dict(engine=read.engine, model=read.model, dpi=read.dpi,
                                                 mean_confidence=read.mean_confidence))
        # 선으로 칸이 나뉜 표가 있으면 그 칸에 든 줄은 칸의 글자가 된다. 나머지 줄은 본문 항목이다.
        grid = [cell for table in cleaned if table["page_no"] == page_no for cell in table["grid"]]
        filled, loose, unsure = assign(read.lines, [cell["bbox"] for cell in grid])
        for cell, got in zip(grid, filled):
            if got["text"] and not cell["text"]:
                cell.update(text=got["text"], confidence=got["confidence"])
        # 칸에 넣지 않은 줄은 인쇄된 높이에 맞춰 기존 항목 사이에 끼운다. 기존 항목끼리의 순서는 바꾸지 않는다.
        tops = {table["table_id"]: table["bbox"][1] for table in cleaned}
        waiting = sorted([(line, None) for line in loose] + [(line, "ambiguous") for line in unsure],
                         key=lambda pair: (round(pair[0].bbox[1] / 6), pair[0].bbox[0]))
        merged = []

        def place(before=None):
            nonlocal numbered
            while waiting and (before is None or (waiting[0][0].bbox[1] + waiting[0][0].bbox[3]) / 2 < before):
                line, cell_assignment = waiting.pop(0)
                numbered += 1
                item = dict(kind="paragraph", content_id=numbered, text=line.text,
                            bbox=[round(v, 1) for v in line.bbox], confidence=line.confidence)
                if cell_assignment:  # 표 위에 있지만 칸을 하나로 정하지 못한 줄
                    item["cell_assignment"] = cell_assignment
                merged.append(item)

        for item in page["items"]:
            top = tops.get(item.get("table_id")) if item["kind"] == "table" else (item.get("bbox") or [None, None])[1]
            if top is not None:
                place(top)
            merged.append(item)
        place()
        for position, item in enumerate(merged, 1):
            item["position"] = position
        page["items"] = merged
    links = []
    for link in doc.context_links:
        source, target = link.source, link.target_id
        item = dict(scope=link.scope, relation=link.relation,
                    content_id=by_ref.get((source.page_number, source.block_id, source.span_id)), state=link.status)
        if target in tables:
            item["table_id"] = tables[target]
        elif target in node_ref:  # 주석이 표의 한 행·열에 붙은 경우
            item["table_id"], item["node"] = node_ref[target]
        elif target:
            item["target_ref"] = target.replace(doc.document_id + "/", "")
        links.append(item)
    cell_ref = {c.cell_id: [tables[t.table_id], n] for t in doc.physical_tables for n, c in enumerate(t.cells, 1)}

    def refs(ids):
        return [cell_ref[c] for c in ids if c in cell_ref]

    continuations = []
    for joined in doc.logical_table_links:
        reasons, paths = list(joined.reason_codes), []
        for path in joined.paths:
            if path.cell_id not in cell_ref:
                continue
            item = dict(cell_id=cell_ref[path.cell_id][1], row=refs(path.row_source_cell_ids),
                        column=refs(path.column_source_cell_ids), state=path.state)
            if path.row_candidate_cell_ids:
                item["row_candidates"] = refs(path.row_candidate_cell_ids)
            if list(path.reasons) != reasons:
                item["reasons"] = list(path.reasons)
            paths.append(item)
        continuations.append(dict(
            from_table=tables.get(joined.source_table_id), to_table=tables.get(joined.target_table_id), kind=joined.kind,
            state=joined.status, reasons=reasons, header_cells=refs(joined.header_source_cell_ids),
            row_cells=refs(joined.inherited_row_source_cell_ids),
            candidate_tables=[tables[t] for t in joined.candidate_source_table_ids if t in tables], paths=paths))
    return dict(
        format=FORMAT,
        document=dict(document_id=doc.document_id, pages=len(pages), tables=len(tables),
                      validation_status=doc.validation_status, facts_published=False),
        sections=outline,
        pages=[pages[n] for n in sorted(pages, key=lambda n: (n is None, n))],
        tables=cleaned,
        links=links, table_links=continuations)


def main(argv=None):
    from doc_understanding.domain.pipeline_contracts import CanonicalDocument

    source, target = map(Path, (argv or sys.argv[1:])[:2])
    doc = CanonicalDocument.model_validate_json(source.read_bytes())
    clean = clean_document(doc)
    CleanDocument.model_validate(clean)  # 규격에 맞지 않는 출력은 쓰지 않는다
    text = json.dumps(clean, ensure_ascii=False, indent=1)
    target.write_text(text, encoding="utf8")
    print(json.dumps(dict(source_bytes=source.stat().st_size, clean_bytes=len(text.encode("utf8")))))


if __name__ == "__main__":
    main()
