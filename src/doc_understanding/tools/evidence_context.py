"""Read-only source identity and context closure for review exports."""


def parent_identities(doc, nested):
    result = []
    pages = {p["page_number"]: p for p in doc.get("document_ir", {}).get("pages", [])}
    for link in nested.get("cells", []):
        page = pages.get(link["page_number"], {})
        native = next(
            (
                c
                for t in page.get("tables", [])
                for c in t["cells"]
                if c["cell_id"] == link["parent_cell_id"]
            ),
            None,
        )
        nonblank = {
            s["span_id"]
            for b in page.get("blocks", [])
            for s in b.get("spans", [])
            if s["text_raw"].strip()
        }
        candidates = []
        for table in doc["physical_tables"]:
            if table["page_number"] != link["page_number"]:
                continue
            for cell in table["cells"]:
                direct = cell["cell_id"] == link["parent_cell_id"]
                same = native and set(native["span_refs"]) & nonblank
                same = same and (
                    set(native["span_refs"]) & nonblank == set(cell["span_refs"]) & nonblank
                )
                same_box = (
                    native
                    and max(
                        abs(cell["source_bbox"][k] - native["source"]["bbox"][k])
                        for k in ("x0", "y0", "x1", "y1")
                    )
                    <= 1.5
                )
                if direct or same_box:
                    candidates.append(
                        dict(
                            table_id=table["table_id"],
                            cell_id=cell["cell_id"],
                            identity_supported=bool(direct or same),
                        )
                    )
        result.append(
            dict(
                native_parent_cell_id=link["parent_cell_id"],
                page_number=link["page_number"],
                candidates=candidates,
                status="matched"
                if len(candidates) == 1 and candidates[0]["identity_supported"]
                else "unresolved",
                method="exact_nonblank_spans_and_bbox_or_same_id",
            )
        )
    return result


def context_evidence(doc, nested, selected_ids):
    identities = parent_identities(doc, nested)
    identity = {v["native_parent_cell_id"]: v for v in identities}
    selected = set(selected_ids)
    included = set(selected)
    links = []
    logical = []
    # Both directions retain the parent when a child is selected and vice versa.
    changed = True
    while changed:
        changed = False
        for link in nested.get("cells", []):
            ids = set(link["child_table_ids"])
            ids.update(v["table_id"] for v in identity[link["parent_cell_id"]]["candidates"])
            if ids & included:
                if link not in links:
                    links.append(link)
                if ids - included:
                    included |= ids
                    changed = True
        for link in doc.get("logical_table_links", []):
            if link["target_table_id"] in included:
                if link not in logical:
                    logical.append(link)
                if link["source_table_id"] not in included:
                    included.add(link["source_table_id"])
                    changed = True
    native_ids = {v["parent_native_table_id"] for v in links}
    return dict(
        nested_context=links,
        nested_parent_identity=[identity[v["parent_cell_id"]] for v in links],
        related_physical_tables=[
            t for t in doc["physical_tables"] if t["table_id"] in included - selected
        ],
        related_structured_tables=[
            t for t in doc["table_structure"] if t["table_id"] in included - selected
        ],
        related_native_tables=[
            t
            for p in doc.get("document_ir", {}).get("pages", [])
            for t in p["tables"]
            if t["table_candidate_id"] in native_ids
        ],
        logical_table_links=logical,
    )
