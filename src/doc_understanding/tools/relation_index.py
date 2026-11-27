"""tools / relation_index."""

from doc_understanding.tools import evidence_context


class RelationIndex:
    """Presence in a frozen extraction is not correctness against the PDF."""

    def __init__(self, doc, nested):
        self.doc = doc
        self.nested = nested
        self.sources = {}
        self.edges = []
        self.unknown_children = set()
        for t in doc["physical_tables"]:
            self.sources[t["table_id"]] = dict(
                kind="table", page=t["page_number"], bbox=t.get("bounding_box")
            )
            for c in t["cells"]:
                self.sources[c["cell_id"]] = dict(
                    kind="cell",
                    table_id=t["table_id"],
                    page=t["page_number"],
                    text=c.get("original_text", ""),
                    bbox=c["source_bbox"],
                )
        identities = {
            x["native_parent_cell_id"]: x for x in evidence_context.parent_identities(doc, nested)
        }
        for link in nested.get("cells", []):
            native = link["parent_cell_id"]
            identity = identities[native]
            parents = [native] if native in self.sources else []
            if identity["status"] == "matched":
                parents += [x["cell_id"] for x in identity["candidates"] if x["identity_supported"]]
            if not parents:
                self.unknown_children.update(link["child_table_ids"])
            for p in set(parents):
                for child in link["child_table_ids"]:
                    self.edges.append(
                        dict(
                            relation="parent_table",
                            source_id=p,
                            target_id=child,
                            provenance=dict(
                                native_parent_cell_id=native, identity_status=identity["status"]
                            ),
                        )
                    )
        for st in doc.get("table_structure", []):
            nodes = {
                n["node_id"]: n.get("source_cell_id")
                for n in st.get("row_tree", []) + st.get("column_tree", [])
            }
            for m in st.get("cell_mappings", []):
                for axis, key in [("row_header", "row_path"), ("column_header", "column_path")]:
                    for n in m.get(key, []):
                        if nodes.get(n):
                            self.edges.append(
                                dict(
                                    relation=axis,
                                    source_id=nodes[n],
                                    target_id=m["cell_id"],
                                    provenance=dict(node_id=n, table_id=st["table_id"]),
                                )
                            )
        for link in doc.get("logical_table_links", []):
            self.edges.append(
                dict(
                    relation="continuation",
                    source_id=link["source_table_id"],
                    target_id=link["target_table_id"],
                    provenance=link,
                )
            )

    def lookup(self, relation, source, target):
        matches = [
            e
            for e in self.edges
            if (e["relation"], e["source_id"], e["target_id"]) == (relation, source, target)
        ]
        status = "present" if matches else "not_found_in_snapshot"
        if not matches and (
            source not in self.sources
            or target not in self.sources
            or relation not in ("parent_table", "row_header", "column_header", "continuation")
            or (relation == "parent_table" and target in self.unknown_children)
        ):
            status = "unknown"
        return dict(
            relation=relation,
            source_id=source,
            target_id=target,
            status=status,
            matches=matches,
            semantic_verified=False,
            scope="stored extraction relations; absence is not source-PDF absence",
        )


def lookup_claims(analysis, index):
    results = []
    for i in (analysis or {}).get("issues", []):
        if i["relation"] in ("parent_table", "row_header", "column_header", "continuation"):
            result = index.lookup(i["relation"], i["source_id"], i["target_id"])
            results.append(
                dict(
                    issue_id=i["issue_id"],
                    claim=i["claim"],
                    contradiction=i["claim"] == "missing" and result["status"] == "present",
                    **result,
                )
            )
    return results


def check_stored_claims(analysis, index):
    return [
        dict(
            claim=c,
            lookup=(result := index.lookup(c["relation"], c["source_id"], c["target_id"])),
            contradiction=c["stored_status"] != result["status"],
        )
        for c in (analysis or {}).get("stored_claims", [])
    ]
