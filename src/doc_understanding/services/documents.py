"""Document extraction and display snapshots, independent of HTTP and agent queues."""

import json
from pathlib import Path


def worker(source, out):
    from doc_understanding.parser.pipeline import prepare
    from doc_understanding.services.run_progress import RunProgress

    progress = RunProgress(source, out)
    stage = "extract"
    try:
        progress.stage(stage, "running")
        run, manifest = prepare(source, out / "extraction")
        progress.stage(stage, "completed")
        stage = "export"
        progress.stage(stage, "running")
        export_run(run, out, manifest["status"])
        progress.stage(stage, "completed")
        progress.finish("completed")
    except Exception:
        progress.stage(stage, "failed")
        progress.finish("failed")
        raise


def export_run(run, out, processing_status):
    """Display an existing frozen run without rerunning or editing extraction."""
    from doc_understanding.parser.assembly.document import build_from_run
    from doc_understanding.parser.assembly.nested_content import build_nested_content

    doc = build_from_run(run)
    links = build_nested_content(doc)
    export_document(doc, links, out, processing_status)


def export_document(doc, links, out, processing_status):
    """Export a new review snapshot, including separate logical-path candidates."""
    raw = doc.model_dump(mode="json")
    nested = links.model_dump(mode="json")
    (out / "document.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf8")
    (out / "nested.json").write_text(json.dumps(nested, ensure_ascii=False), encoding="utf8")
    tables = []
    source_cells = {c.cell_id: c for t in doc.physical_tables for c in t.cells}
    logical = {p.cell_id: (p, link.link_id) for link in doc.logical_table_links for p in link.paths}
    for t in doc.physical_tables:
        st = next(s for s in doc.table_structure if s.table_id == t.table_id)
        nodes = {n.node_id: n.label_raw for n in st.row_tree + st.column_tree}
        maps = {m.cell_id: m for m in st.cell_mappings}
        cells = []
        for c in t.cells:
            m = maps.get(c.cell_id)
            cells.append(
                dict(
                    id=c.cell_id,
                    row=c.row_index,
                    col=c.column_index,
                    rs=c.rowspan,
                    cs=c.colspan,
                    text=c.original_text,
                    bbox=c.source_bbox.model_dump(),
                    spans=list(c.span_refs),
                    row_path=[nodes[n] for n in m.row_path] if m else [],
                    col_path=[nodes[n] for n in m.column_path] if m else [],
                    state=m.evidence_state if m else "unresolved",
                )
            )
            if c.cell_id in logical:
                path, link_id = logical[c.cell_id]
                cells[-1].update(
                    raw_row_path=cells[-1]["row_path"],
                    raw_col_path=cells[-1]["col_path"],
                    row_path=[
                        source_cells[s].original_text or "" for s in path.row_source_cell_ids
                    ],
                    col_path=[
                        source_cells[s].original_text or "" for s in path.column_source_cell_ids
                    ],
                    state=path.state,
                    path_basis="logical_link_candidate",
                    logical_link_id=link_id,
                    logical_path=path.model_dump(mode="json"),
                    semantic_verified=False,
                )
        tables.append(dict(id=t.table_id, page=t.page_number, cells=cells, state=st.analysis_state))
    contents = {c.content_id: c.text_raw for c in doc.contents}
    contexts = []
    for link in links.cells:
        contexts.append(
            dict(
                parent=link.parent_cell_id,
                page=link.page_number,
                children=list(link.child_table_ids),
                prose=[contents.get(i.ref_id, "") for i in link.items if i.kind == "content"],
                status=link.status,
                semantic_status=link.semantic_status,
            )
        )
    result = dict(
        tables=tables,
        contexts=contexts,
        pages=[
            dict(number=p.page_number, width=p.width, height=p.height, text_status=p.text_status)
            for p in doc.document_ir.pages
        ],
        document_id=doc.document_id,
        processing_status=processing_status,
    )
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf8")


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    worker(args.source, args.output)


if __name__ == "__main__":
    main()
