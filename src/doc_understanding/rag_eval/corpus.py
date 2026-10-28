"""Read-only IR adapters. Source-specific annotations never enter this module."""
import hashlib
import html
import json
from pathlib import Path

from .engine import Chunk


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build(document, physical, structured, overlay, revisions, pages):
    output = {name: [] for name in ("A", "B", "C-rule", "C-hybrid", "D-rule", "D-hybrid")}
    structures = {s["table_id"]: s for s in structured}
    features = {f["node_id"]: f for f in overlay["features"]}
    rule = {n["semantic_node_id"]: n["semantic_parent_id"] for n in overlay["table_nodes"] if n["semantic_parent_id"]}
    node_cells = {f["node_id"]: f["source_cell_ids"] for f in overlay["features"]}
    def paths(cell_id, parents):
        out = []
        for node, refs in node_cells.items():
            if cell_id not in refs:
                continue
            chain, seen = [], set()
            while node and node not in seen:
                seen.add(node)
                chain.append(features.get(node, {}).get("text_raw", node))
                node = parents.get(node)
            out.append(" > ".join(reversed(chain)))
        return out
    for page in document["pages"]:
        pn = page["page_number"]
        if pn not in pages:
            continue
        tables = [t for t in physical if t["page_number"] == pn]
        # Exact native spans joined in extraction order; keep offsets for fair evidence alignment.
        text, spans = "", []
        for block in page["blocks"]:
            for span in block.get("spans", []):
                value = span.get("text_raw") or ""
                start = len(text)
                text += value+"\n"
                spans.append((start, len(text)-1, span["span_id"]))
        for start in range(0, len(text), 650):
            stop = start+800
            refs = {sid for a, b, sid in spans if start <= a and b <= stop}
            cell_refs = [c["cell_id"] for t in tables for c in t["cells"]
                         if c.get("span_refs") and set(c["span_refs"]) <= refs]
            output["A"].append(Chunk(chunk_id=f"A/p{pn}/{start}", text=text[start:stop], page=pn,
                                     evidence_ids=sorted(refs)+cell_refs))
        # Native page context outside table bounds, no generated title/unit guesses.
        context_blocks = []
        for block in page["blocks"]:
            box = block["source"]["bbox"]
            if any(t["bounding_box"]["y0"] <= (box["y0"]+box["y1"])/2 <= t["bounding_box"]["y1"] for t in tables):
                continue
            context_blocks.append(block)
        context = "\n".join(b.get("text_raw") or "" for b in context_blocks)
        context_ids = [s["span_id"] for b in context_blocks for s in b.get("spans", [])]
        for table in tables:
            tid = table["table_id"]
            st = structures.get(tid, {})
            mappings = {m["cell_id"]: m for m in st.get("cell_mappings", [])}
            nodes = {n["node_id"]: n for axis in ("row_tree", "column_tree") for n in st.get(axis, [])}
            header_rows = max((p["header_rows"] for p in st.get("panels", [])), default=1)
            headers = [c for c in table["cells"] if c["row_index"] < header_rows]
            for row in sorted({c["row_index"] for c in table["cells"]}):
                selected = [c for c in table["cells"] if c["row_index"] <= row < c["row_index"]+c["rowspan"]]
                selected = sorted({c["cell_id"]: c for c in headers+selected}.values(), key=lambda c: (c["row_index"], c["column_index"]))
                markup = "<table>"
                for r in sorted({c["row_index"] for c in selected}):
                    markup += "<tr>"+"".join(f'<td data-column="{c["column_index"]}" rowspan="{c["rowspan"]}" colspan="{c["colspan"]}">{html.escape(c.get("original_text") or "")}</td>' for c in selected if c["row_index"] == r)+"</tr>"
                markup += "</table>"
                base = f"PDF page {pn}\n{context}\n{markup}"
                evidence = context_ids+[c["cell_id"] for c in selected]
                for method in output:
                    if method == "A":
                        continue
                    parents, status = rule, "rule_provisional"
                    if "hybrid" in method:
                        if pn not in revisions:
                            continue  # Explicit missing coverage, never silently substitute Rule.
                        parents, status = revisions[pn]["revision"]["after"], "stored_live_response_c11_replay_provisional"
                    extras = []
                    if method != "B":
                        for c in selected:
                            m = mappings.get(c["cell_id"], {})
                            structural = [nodes[n].get("label_raw") or "" for n in m.get("row_path", [])+m.get("column_path", []) if n in nodes]
                            extras.append(json.dumps({"cell_id": c["cell_id"], "physical_paths": structural,
                                                      "semantic_paths": paths(c["cell_id"], parents),
                                                      "state": m.get("evidence_state", "unresolved")}, ensure_ascii=False))
                    output[method].append(Chunk(chunk_id=f"{method}/{tid}/r{row}", text=base+"\n"+"\n".join(extras), page=pn, table_id=tid,
                                                evidence_ids=evidence, tree_status=status if method != "B" else "none"))
    # Bounded semantic parent/child expansion links; never use reference annotations.
    for method in ("D-rule", "D-hybrid"):
        corpus = output[method]
        for chunk in corpus:
            parents = revisions[chunk.page]["revision"]["after"] if "hybrid" in method else rule
            related = set()
            own = set(chunk.evidence_ids)
            for child, parent in parents.items():
                cr, pr = set(node_cells.get(child, [])), set(node_cells.get(parent, []))
                if cr & own:
                    related |= pr
                if pr & own:
                    related |= cr
            chunk.expansion_ids = [c.chunk_id for c in corpus if c.table_id == chunk.table_id and c.chunk_id != chunk.chunk_id and related & set(c.evidence_ids)]
    return output
