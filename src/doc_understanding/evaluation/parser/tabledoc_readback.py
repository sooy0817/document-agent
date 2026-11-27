"""Read section contents, cell evidence and links from public tabledoc 0.3 through 0.5."""
from collections import defaultdict


def by_id(records, field):
    index = {r[field]: r for r in records}
    if len(index) != len(records):
        raise ValueError(f"duplicate {field}")
    return index


def ancestors(leaf, index):
    chain = []
    while leaf is not None:
        if leaf in chain:
            raise ValueError("cyclic public hierarchy")
        chain.append(leaf)
        leaf = index[leaf]["parent"]
    return tuple(reversed(chain))


def readback(public):
    if public["format"] not in ("tabledoc/0.3", "tabledoc/0.4", "tabledoc/0.5"):  # 0.4는 0.3에 항목만 더했다
        raise ValueError("expected tabledoc/0.3, 0.4 or 0.5")
    sections = by_id(public["sections"], "section_id")
    tables = by_id(public["tables"], "table_id")
    members, subtrees = defaultdict(list), defaultdict(list)
    items, context, values, targets = {}, {}, {}, {}
    printed_titles = defaultdict(set)
    for page in sorted(public["pages"], key=lambda p: p["page_no"]):
        for expected, item in enumerate(sorted(page["items"], key=lambda i: i["position"]), 1):
            if item["position"] != expected:
                raise ValueError("noncontiguous item positions")
            is_table = item["kind"] == "table"
            key = ("table", item["table_id"]) if is_table else ("content", item["content_id"])
            if key in items:
                raise ValueError(f"duplicate item {key}")
            if is_table:
                if tables[key[1]]["page_no"] != page["page_no"]:
                    raise ValueError("table on wrong page")
                context[key[1]] = tuple(item.get("context") or ())
            items[key] = (page["page_no"], item["kind"], item.get("text"),
                          item.get("section_id"), tuple(item.get("inside_tables") or ()))
            sid = item.get("section_id")
            if not is_table and item.get("section_title"):
                printed_titles[sid].add(item["content_id"])
            members[sid].append(key)
            for ancestor in ancestors(sid, sections):
                subtrees[ancestor].append(key)
    for ids in context.values():
        for cid in ids:
            if ("content", cid) not in items:
                raise ValueError("missing context content")
    for tid, table in tables.items():
        cells = by_id(table["grid"], "cell_id")
        paths = by_id(table.get("paths", []), "cell_id")
        axes = {axis: by_id(table.get(axis + "_nodes", []), "node_id") for axis in ("row", "column")}
        for axis, nodes in axes.items():
            for nid in nodes:
                chain = ancestors(nid, nodes)
                label_path = tuple((nodes[n]["cell_id"], nodes[n]["label_raw"]) for n in chain)
                if any(cid is not None and cid not in cells for cid, _ in label_path):
                    raise ValueError("unknown label cell")
                if (tid, nid) in targets:
                    raise ValueError("ambiguous node number")
                targets[tid, nid] = (axis, label_path)
        for cid in paths.keys() | {n for n, cell in cells.items() if cell["role"] == "value"}:
            cell, path = cells[cid], paths.get(cid, {})
            def axis_path(axis):
                nid = path.get(axis + "_node")
                if nid is None:
                    return ()
                actual_axis, labels = targets[tid, nid]
                if actual_axis != axis:
                    raise ValueError("wrong axis for path")
                return labels
            values[tid, cid] = (cell["text"], table["page_no"], tuple(cell["bbox"]),
                                axis_path("row"), axis_path("column"), path.get("state"))
    notes = []
    for link in public["links"]:
        if link["relation"] != "note":
            continue
        tid, cid, node = link.get("table_id"), link.get("content_id"), link.get("node")
        if cid is not None and ("content", cid) not in items:
            raise ValueError("missing note source")
        if tid is not None and tid not in tables:
            raise ValueError("missing note table")
        target = targets[tid, node] if node is not None else None
        notes.append((cid, tid, target, link["scope"], link["state"], link.get("target_ref")))
    for sid, section in sections.items():
        for cid in section.get("reprinted_title_content_ids", []):
            if items["content", cid][3] != sid or cid in printed_titles[sid]:
                raise ValueError("invalid reprinted title")
            printed_titles[sid].add(cid)
    grid = {(tid, c["cell_id"]): (c["text"], t["page_no"], tuple(c.get("bbox", ())))
            for tid, t in tables.items() for c in t["grid"]}
    def refs(pairs):
        keys = tuple(tuple(pair) for pair in pairs)
        if any(k not in grid for k in keys):
            raise ValueError("missing linked cell")
        return keys
    details = {}
    joins = []
    for link in public["table_links"]:
        src, dst = link["from_table"], link["to_table"]
        key = (src, dst)
        candidates = tuple(link.get("candidate_tables", []))
        if key in details or any(n not in tables for n in candidates):
            raise ValueError("invalid table link")
        paths = {}
        for path in link.get("paths", []):
            cid = path["cell_id"]
            # A candidate continuation can disagree with the fragment's local cell role.
            if (dst, cid) not in grid or cid in paths or (link["state"] == "accepted" and (dst, cid) not in values):
                raise ValueError("invalid linked value")
            reasons = link["reasons"] if path.get("reasons") is None else path["reasons"]
            paths[cid] = (refs(path.get("row", [])), refs(path.get("column", [])), refs(path.get("row_candidates", [])),
                          path["state"], tuple(reasons))
        details[key] = (refs(link.get("header_cells", [])), refs(link.get("row_cells", [])), candidates, paths)
        joins.append((src, dst, tables[src]["page_no"], tables[dst]["page_no"],
                      link["kind"], link["state"], tuple(link["reasons"])))
    return dict(sections=sections, members=members, subtrees=subtrees, items=items, printed_titles=printed_titles,
                context=context, values=values, notes=notes, joins=joins, details=details, grid=grid)
