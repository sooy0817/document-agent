"""Compare independently decoded public tabledoc JSON with its CanonicalDocument oracle.

Input manifest: [{"label": "...", "group": "...", "path": "canonical.json"}].
When a record supplies public_path, compare that exact export, including invalid exports.
An invalid generated projection is inspected diagnostically and never marked publishable.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from doc_understanding.domain.pipeline_contracts import CanonicalDocument
from doc_understanding.evaluation.parser.tabledoc_readback import readback
from doc_understanding.export.clean import clean_document
from doc_understanding.export.models import CleanDocument


def compare(doc, view):
    counts, misses = Counter(), defaultdict(list)

    def check(name, key, condition):
        counts[name] += 1
        if not condition:
            misses[name].append(key)

    table_ids = {t.table_id: i for i, t in enumerate(doc.physical_tables, 1)}
    content_ids = {c.content_id: i for i, c in enumerate(doc.contents, 1)}
    ordered = sorted(doc.sections, key=lambda s: s.reading_order)
    section_ids = {s.section_id: i for i, s in enumerate(ordered, 1)}
    content_keys, ref_ids = {}, defaultdict(set)
    for c in doc.contents:
        key = ("table", table_ids[c.table_id]) if c.kind == "table" else ("content", content_ids[c.content_id])
        content_keys[c.content_id] = key
        page = c.source_references[0].page_number
        expected = (page, c.kind, c.text_raw, section_ids.get(c.section_id),
                    tuple(table_ids[t] for t in c.duplicate_of_table_ids))
        check("item", key, view["items"].get(key) == expected)
        if c.kind == "table":
            counts["tables"] += 1
            counts["table_without_section"] += c.section_id is None
            check("table_context", key, view["context"].get(key[1]) ==
                  tuple(content_ids[n] for n in c.context_content_ids))
        else:
            for r in c.source_references:
                ref_ids[(r.page_number, r.block_id, r.span_id)].add(key[1])
    check("item_coverage", "document", set(content_keys.values()) == set(view["items"]))
    children = defaultdict(list)
    for s in ordered:
        children[s.parent_id].append(s)
    def descendants(s):
        yield from s.content_ids
        for child in children[s.section_id]:
            yield from descendants(child)
    positions = {c.content_id: c.reading_order for c in doc.contents}
    source_pages = {c.content_id: c.source_references[0].page_number for c in doc.contents}
    for s in ordered:
        sid = section_ids[s.section_id]
        actual = view["sections"][sid]
        direct = [content_keys[k] for k in sorted(s.content_ids, key=positions.get)]
        check("section_direct", sid, direct == view["members"][sid])
        subtree = [content_keys[k] for k in sorted(descendants(s), key=positions.get)]
        check("section_subtree", sid, subtree == view["subtrees"][sid])
        pages = [source_pages[c] for c in s.content_ids]
        bounds = (min(pages), max(pages)) if pages else (None, None)
        title_key = ("content", actual["title_content_id"])
        check("outline", sid, title_key in view["items"] and view["items"][title_key][2] == s.title_raw
              and (s.title_raw, section_ids.get(s.parent_id), s.evidence_state,
              content_ids.get(s.section_id.removesuffix("/section")), *bounds) ==
              (actual["title"], actual["parent"], actual["state"], actual["title_content_id"],
               actual["first_page"], actual["last_page"]))
        counts["spanning_sections"] += bounds[0] != bounds[1]
        printed = set()
        for ref in s.source_references:
            printed.update(ref_ids[(ref.page_number, ref.block_id, ref.span_id)])
        check("printed_title_refs", sid, printed == view["printed_titles"][sid])
        counts["reprinted_titles"] += max(0, len(printed) - 1)
    check("section_coverage", "document", set(section_ids.values()) == set(view["sections"]))
    local_paths, node_targets, cell_refs = {}, {}, {}
    structures = {s.table_id: s for s in doc.table_structure}
    for t in doc.physical_tables:
        tid, st = table_ids[t.table_id], structures[t.table_id]
        cells = {c.cell_id: i for i, c in enumerate(t.cells, 1)}
        cell_refs.update({cid: (tid, n) for cid, n in cells.items()})
        raw = {c.cell_id: c for c in t.cells}
        for cid, c in raw.items():
            check("grid_evidence", (tid, cells[cid]), view["grid"].get((tid, cells[cid])) ==
                  (c.original_text, t.page_number, tuple(round(n, 1) for n in c.source_bbox.xyxy)))
        nodes = {n.node_id: n for n in (*st.row_tree, *st.column_tree)}
        def label_path(ids):
            return tuple((cells[nodes[n].source_cell_id], nodes[n].label_raw) for n in ids)
        for nid, node in nodes.items():
            chain = [nid]
            while nodes[chain[-1]].parent_id:
                chain.append(nodes[chain[-1]].parent_id)
            node_targets[nid] = (tid, (node.axis, label_path(reversed(chain))))
        mappings = {m.cell_id: m for m in st.cell_mappings}
        for cid in {r.cell_id for r in st.cell_roles if r.role == "value"} | mappings.keys():
            m, c = mappings.get(cid), raw[cid]
            key = (tid, cells[cid])
            local_paths[key] = m
            v = view["values"].get(key)
            box = tuple(c.source_bbox.xyxy)
            check("cell_source", key, v is not None and v[:2] == (c.original_text, t.page_number)
                  and v[2] == tuple(round(n, 1) for n in box))
            counts["bbox_rounded"] += bool(v and box != v[2])
            expected_path = (label_path(m.row_path), label_path(m.column_path), m.evidence_state) if m else ((), (), None)
            check("local_paths", key, v is not None and v[3:] == expected_path)
            counts["local_both_labeled"] += bool(v and v[3] and v[4] and
                                                 all(s and s.strip() for _, s in v[3] + v[4]))
            counts["local_missing_axis"] += not (m and m.row_path and m.column_path)
            counts["local_blank_label"] += bool(m and m.row_path and m.column_path and
                any(not (nodes[n].label_raw or "").strip() for n in (*m.row_path, *m.column_path)))
    check("value_coverage", "document", local_paths.keys() == view["values"].keys())
    expected_notes, known_sources = Counter(), set()
    for link in doc.context_links:
        if link.relation != "note":
            continue
        r = link.source
        sources = ref_ids[(r.page_number, r.block_id, r.span_id)]
        source = next(iter(sources)) if len(sources) == 1 else None
        tid, node = node_targets.get(link.target_id, (table_ids.get(link.target_id), None))
        if tid is not None:
            known_sources.update(sources)
        counts["note_" + (node[0] if node else "table" if tid else "unresolved")] += 1
        counts["ambiguous_note_source"] += len(sources) != 1
        expected_notes[(source, tid, node, link.scope, link.status,
                        link.target_id.removeprefix(doc.document_id + "/") if link.target_id and tid is None else None)] += 1
    check("note_links", "document", expected_notes == Counter(view["notes"]))
    counts["printed_notes"] = len(doc.notes)
    counts["printed_notes_without_target"] = sum(content_ids[n.content_id] not in known_sources for n in doc.notes)
    expected_joins = []
    tpages = {t.table_id: t.page_number for t in doc.physical_tables}
    for link in doc.logical_table_links:
        expected_joins.append((table_ids[link.source_table_id], table_ids[link.target_table_id],
                               tpages[link.source_table_id], tpages[link.target_table_id], link.kind,
                               link.status, tuple(link.reason_codes)))
        crosses_page = tpages[link.source_table_id] != tpages[link.target_table_id]
        if crosses_page:
            counts["cross_page_" + link.status] += 1
        key = table_ids[link.source_table_id], table_ids[link.target_table_id]
        headers, rows, alternatives, paths = view["details"][key]
        def refs(ids):
            return tuple(cell_refs[c] for c in ids)
        check("link_sources", key, (headers, rows, alternatives) == (
            refs(link.header_source_cell_ids), refs(link.inherited_row_source_cell_ids),
            tuple(table_ids[t] for t in link.candidate_source_table_ids)))
        counts["header_sources"] += len(headers)
        counts["alternative_sources"] += len(alternatives)
        expected = {cell_refs[p.cell_id][1]: (refs(p.row_source_cell_ids), refs(p.column_source_cell_ids),
                    refs(p.row_candidate_cell_ids), p.state, tuple(p.reasons)) for p in link.paths}
        check("linked_value_paths", key, expected == paths)
        counts["linked_values"] += len(paths)
        counts["candidate_paths_outside_local_values"] += sum(
            link.status == "candidate" and (key[1], cid) not in view["values"] for cid in paths)
    check("table_links", "document", Counter(expected_joins) == Counter(view["joins"]))
    return dict(counts=dict(counts), mismatches={k: dict(count=len(v), examples=v[:5]) for k, v in misses.items()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--exports", type=Path, help="Keep the serialized projections separately from internal documents")
    args = parser.parse_args()
    records = []
    for e in json.loads(args.manifest.read_bytes()):
        raw = Path(e["path"]).read_bytes()
        doc = CanonicalDocument.model_validate_json(raw)
        output = (Path(e["public_path"]).read_bytes() if "public_path" in e else
                  json.dumps(clean_document(doc), ensure_ascii=False).encode())
        try:
            CleanDocument.model_validate_json(output)
            errors = []
        except ValidationError as exc:
            errors = [dict(loc=x["loc"], type=x["type"]) for x in exc.errors()]
        if args.exports:
            args.exports.mkdir(parents=True, exist_ok=True)
            suffix = ".invalid.json" if errors else ".json"
            (args.exports / (doc.document_ir.source_sha256 + suffix)).write_bytes(output)
        try:
            result = compare(doc, readback(json.loads(output)))
        except (KeyError, ValueError, TypeError, IndexError) as exc:
            result = dict(counts={}, mismatches={"readback": f"{type(exc).__name__}: {exc}"})
        records.append(dict(group=e["group"], label=e["label"], document_id=doc.document_id,
                            canonical_sha256=hashlib.sha256(raw).hexdigest(),
                            schema_errors=errors, **result))
        print(e["label"], "schema errors", len(errors), "mismatches", result["mismatches"], flush=True)
    args.out.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf8")
    return int(any(r["schema_errors"] or r["mismatches"] or
                   any(n for k, n in r["counts"].items() if "_omitted" in k)
                   for r in records))


if __name__ == "__main__":
    raise SystemExit(main())
