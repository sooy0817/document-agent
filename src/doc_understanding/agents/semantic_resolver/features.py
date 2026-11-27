"""Extract only source-backed typography/position; marker families have no fixed depth."""

import re
from collections import defaultdict
from statistics import median
from doc_understanding.domain.text_markers import compact, number_marker, roles  # noqa: F401  (기존 import 경로 유지)
from .models import SemanticFeature, RelationCandidate


def numeric(text):
    return bool(re.fullmatch(r"[\s\d,().%+\-△▲]+", text or ""))


def inside(a, b):
    return b.x0 <= a.x0 and a.x1 <= b.x1 and b.y0 <= a.y0 and a.y1 <= b.y1


def extract_features(document, physical, structured):
    source_nodes = {n.source_cell_id: n for s in structured for n in s.row_tree + s.column_tree}
    node_lookup = {n.node_id: n for s in structured for n in s.row_tree + s.column_tree}
    pages = {p.page_number: p for p in document.pages}
    features = []
    continuation = []
    table_meta = []
    real_tables = []
    for t in sorted(physical, key=lambda t: (t.page_number, t.bounding_box.y0)):
        # Single-column boxed prose is not coerced into table rows.
        if len(t.columns) < 2 or not t.rows:
            continue
        real_tables.append(t)
        usable = [c for c in t.cells if c.row_index is not None and c.column_index is not None]
        top = sorted([c for c in usable if c.row_index == 0], key=lambda c: c.column_index)
        if not top:
            continue
        corner = top[0]
        header = corner.rowspan or 1
        starts = [corner.column_index] + [
            c.column_index
            for c in top[1:]
            if compact(c.original_text or "")
            and compact(c.original_text or "") == compact(corner.original_text or "")
        ]
        starts = sorted(set(starts))
        ends = starts[1:] + [len(t.columns)]
        header_signature = tuple(compact(c.original_text or "") for c in top)
        group = t.table_id
        previous = None
        for meta in reversed(table_meta):
            old = meta["table"]
            if old.page_number != t.page_number - 1:
                continue
            if meta["header"] != header_signature or len(old.columns) != len(t.columns):
                continue
            old_width = old.bounding_box.x1 - old.bounding_box.x0
            width = t.bounding_box.x1 - t.bounding_box.x0
            same = (
                max(
                    abs(
                        (a.start - old.bounding_box.x0) / old_width
                        - (b.start - t.bounding_box.x0) / width
                    )
                    for a, b in zip(old.columns, t.columns)
                )
                < 0.02
            )
            if (
                same
                and old.bounding_box.y1 > pages[old.page_number].height * 0.75
                and t.bounding_box.y0 < pages[t.page_number].height * 0.3
            ):
                previous = meta
                group = meta["group"]
                break
        current = []
        spans = [s for b in pages[t.page_number].blocks for s in b.spans]
        for pi, (start, end) in enumerate(zip(starts, ends)):
            pc = next(c for c in top if c.column_index == start)
            stub = min(end, start + (pc.colspan or 1))
            panel = f"{group}/panel{pi}"
            # Extra narrative columns allow date/meeting->agenda candidates without
            # declaring every text-valued data cell a label.
            text_columns = set()
            for col in range(stub, end):
                body = [
                    c
                    for c in usable
                    if c.column_index == col
                    and c.row_index >= header
                    and (c.original_text or "").strip()
                ]
                if (
                    body
                    and sum(
                        len(c.original_text or "") > 14 and not numeric(c.original_text)
                        for c in body
                    )
                    / len(body)
                    > 0.5
                ):
                    text_columns.add(col)
            repeated_rows = set()
            for row in range(header, len(t.rows)):
                rowtexts = tuple(
                    compact(c.original_text or "")
                    for c in sorted(
                        [c for c in usable if c.row_index == row], key=lambda c: c.column_index
                    )
                )
                if rowtexts == header_signature:
                    repeated_rows.add(row)
            for c in usable:
                if not start <= c.column_index < end or not (c.original_text or "").strip():
                    continue
                if c.row_index in repeated_rows:
                    continue
                is_header = c.row_index < header
                if not is_header and c.column_index >= stub and c.column_index not in text_columns:
                    continue
                if is_header and c.column_index < stub:
                    continue
                sn = source_nodes.get(c.cell_id)
                matches = [s for s in spans if inside(s.source.bbox, c.source_bbox)]
                sizes = tuple(sorted({s.font_size for s in matches}))
                flags = tuple(sorted({s.flags for s in matches}))
                raw_span = next((s for s in matches if s.text_raw.strip()), None)
                leading = (
                    len(raw_span.text_raw) - len(raw_span.text_raw.lstrip()) if raw_span else 0
                )
                indent = (
                    (
                        raw_span.source.bbox.x0
                        - c.source_bbox.x0
                        + leading * raw_span.font_size * 0.5
                    )
                    if raw_span
                    else None
                )
                parent = node_lookup.get(sn.parent_id) if sn else None
                value_ids = tuple(
                    v.cell_id
                    for v in usable
                    if v.row_index == c.row_index
                    and stub <= v.column_index < end
                    and v.cell_id != c.cell_id
                )
                f = SemanticFeature(
                    node_id=f"overlay/{c.cell_id}",
                    source_node_id=sn.node_id if sn else None,
                    source_cell_ids=(c.cell_id,),
                    source_refs=(c.source,),
                    text_raw=c.original_text,
                    axis="column" if is_header else "row",
                    table_id=t.table_id,
                    section_id=group,
                    panel_id=panel,
                    page_number=t.page_number,
                    order=(t.page_number, c.row_index, c.column_index),
                    row_range=(c.row_index, c.row_index + (c.rowspan or 1)),
                    column_range=(c.column_index, c.column_index + (c.colspan or 1)),
                    physical_parent_id=f"overlay/{parent.source_cell_id}" if parent else None,
                    physical_evidence="observed" if c.status == "resolved" else c.status,
                    number=number_marker(c.original_text),
                    indent=indent,
                    fonts=tuple(sorted({s.font for s in matches})),
                    font_sizes=sizes,
                    font_flags=flags,
                    bold=any(flag & 16 for flag in flags) if flags else None,
                    role_candidates=roles(c.original_text),
                    value_cell_ids=value_ids,
                    page_edge="start"
                    if c.row_index <= header + 1
                    else "end"
                    if c.row_index >= len(t.rows) - 2
                    else "interior",
                    alignment_evidence="contained_native_span"
                    if matches
                    else "unavailable_or_cross_cell_span",
                )
                current.append(f)
            if previous:
                oldnodes = [
                    f for f in previous["features"] if f.axis == "row" and f.panel_id == panel
                ]
                newnodes = [f for f in current if f.axis == "row" and f.panel_id == panel]
                if oldnodes and newnodes:
                    a, b = oldnodes[-1], newnodes[0]
                    continuation.append(
                        RelationCandidate(
                            candidate_id=f"continuation/{a.node_id}/{b.node_id}",
                            parent_id=a.node_id,
                            child_id=b.node_id,
                            relation_type="continuation",
                            evidence_ids=a.source_cell_ids + b.source_cell_ids,
                            methods=(
                                "adjacent_pages",
                                "repeated_header",
                                "aligned_columns",
                                "page_edge",
                            ),
                            conflicts=("independent_semantic_review_pending",),
                        )
                    )
        features.extend(current)
        table_meta.append(dict(table=t, header=header_signature, group=group, features=current))
    # Paragraphs and headings remain document nodes, including boxed narrative pages.
    for page in document.pages:
        textblocks = [b for b in page.blocks if b.kind == "text" and (b.text_raw or "").strip()]
        allsizes = [s.font_size for b in textblocks for s in b.spans]
        typical = median(allsizes) if allsizes else 0
        for i, b in enumerate(textblocks):
            if any(
                inside(b.source.bbox, t.bounding_box)
                for t in real_tables
                if t.page_number == page.page_number
            ):
                continue
            if b.source.bbox.y0 > page.height * 0.96:
                continue
            sizes = tuple(sorted({s.font_size for s in b.spans}))
            marker = number_marker(b.text_raw)
            heading = (marker is not None or max(sizes, default=0) > typical * 1.15) and len(
                b.text_raw
            ) < 150
            features.append(
                SemanticFeature(
                    node_id=f"overlay/{b.block_id}",
                    source_node_id=b.block_id,
                    source_cell_ids=(),
                    source_refs=(b.source,),
                    text_raw=b.text_raw,
                    axis="document",
                    table_id=None,
                    section_id=f"{document.document_id}/document",
                    panel_id="document",
                    page_number=page.page_number,
                    order=(page.page_number, i, 0),
                    physical_evidence="observed",
                    number=marker if heading else None,
                    indent=b.source.bbox.x0,
                    fonts=tuple(sorted({s.font for s in b.spans})),
                    font_sizes=sizes,
                    font_flags=tuple(sorted({s.flags for s in b.spans})),
                    bold=any(s.flags & 16 for s in b.spans),
                    role_candidates=("heading_candidate",) if heading else ("paragraph",),
                    alignment_evidence="native_block",
                )
            )
    repeats = defaultdict(list)
    for f in features:
        repeats[(f.section_id, f.panel_id, compact(f.text_raw))].append(f.node_id)
    features = [
        f.model_copy(
            update={
                "repeated_node_ids": tuple(
                    n
                    for n in repeats[(f.section_id, f.panel_id, compact(f.text_raw))]
                    if n != f.node_id
                )
            }
        )
        for f in features
    ]
    return tuple(features), tuple(continuation)
