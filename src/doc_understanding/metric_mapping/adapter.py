"""Join by source SHA and physical anchors, never by institution/page special cases."""
import hashlib
import json
import re
from pathlib import Path

from .core import compact, overlap


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def anchor(cell, role, artifact_hash):
    return dict(artifact_sha256=artifact_hash, page_number=cell["source"]["page_number"],
                block_id=cell["source"].get("block_id"), cell_id=cell.get("cell_id"),
                bbox=cell["source"]["bbox"], raw_text=cell.get("text_raw") or "", role=role)


def block_ref(block, role, artifact_hash):
    return dict(artifact_sha256=artifact_hash,page_number=block["source"]["page_number"],
                block_id=block["block_id"],bbox=block["source"]["bbox"],
                raw_text=block["text_raw"] or "",role=role)


def auxiliary(source_sha):
    """Existing interpretation is optional candidate evidence, not a new Agent run."""
    from doc_understanding.agent_contracts.models import CanonicalSnapshot
    from doc_understanding.semantic_description.models import SemanticDescriptionBundle, validate_descriptions
    result = []
    for path in Path("data/semantic-understanding/cases").glob("*/canonical.json"):
        obj = json.loads(path.read_text(encoding="utf-8"))
        if not any(a.get("document_sha256") == source_sha for a in obj["evidence"]["artifacts"]):
            continue
        desc_path = path.with_name("descriptions.json")
        if not desc_path.exists():
            continue
        canonical = CanonicalSnapshot.model_validate(obj)
        bundle = SemanticDescriptionBundle.model_validate_json(desc_path.read_bytes())
        validation = validate_descriptions(canonical.evidence, canonical.proposal, bundle)
        result.append(dict(path=str(path),sha256=digest(path),description_path=str(desc_path),
                           description_sha256=digest(desc_path),canonical=obj,
                           descriptions=bundle.model_dump(mode="json"),
                           consistent=validation["structurally_consistent"]))
    return result


def proposal_context(cell, auxiliary_items):
    found = []
    for a in auxiliary_items:
        obj=a["canonical"]
        ids = {e["evidence_id"] for e in obj["evidence"]["items"]
               if e.get("kind")=="cell" and e.get("page")==cell["source"]["page_number"]
               and e.get("bbox") and overlap(dict(zip(("x0","y0","x1","y1"),e["bbox"])),
                                             cell["source"]["bbox"]) > .8
               and compact(e.get("text_raw"))==compact(cell["text_raw"])}
        nodes = {n["node_id"] for n in obj["proposal"]["nodes"]
                 if any(anchor["evidence_id"] in ids for anchor in n["anchors"])}
        if not nodes:
            continue
        relations = [r for r in obj["proposal"]["relations"]
                     if r["source"] in nodes or set(r["targets"]) & nodes]
        descriptions = [d for d in a["descriptions"]["descriptions"]
                        if set(d["subject_ids"]) & nodes]
        node_map={n["node_id"]:n for n in obj["proposal"]["nodes"]}
        parents=[node_map[t].get("label_proposed") for r in relations
                 if r["type"]=="CHILD_OF" and r["source"] in nodes
                 for t in r["targets"] if t in node_map]
        found.append(dict(artifact_path=a["path"],artifact_sha256=a["sha256"],
                          description_path=a["description_path"],
                          description_sha256=a["description_sha256"],
                          consistent=a["consistent"],node_ids=sorted(nodes),
                          parent_labels=parents,
                          relations=relations,descriptions=descriptions,
                          status="existing_unreviewed_interpretation_not_gold"))
    return found


def structured_context(cell, structures):
    result = []
    for physical, structured, path in structures:
        if physical["page_number"] != cell["source"]["page_number"]:
            continue
        matches = [c for c in physical["cells"] if cell["cell_id"] in c["source_cell_ids"]
                   or (overlap(c["source_bbox"],cell["source"]["bbox"])>.8
                       and compact(c.get("original_text"))==compact(cell["text_raw"]))]
        for match in matches:
            nodes = {n["node_id"]:n for n in structured["row_tree"]+structured["column_tree"]}
            mappings = [m for m in structured["cell_mappings"] if m["cell_id"]==match["cell_id"]]
            rownodes = [n for n in structured["row_tree"] if n["source_cell_id"]==match["cell_id"]]
            parents=[]
            for node in rownodes:
                seen=set()
                parent=node["parent_id"]
                while parent in nodes and parent not in seen:
                    seen.add(parent)
                    parents.append(nodes[parent]["label_raw"])
                    parent=nodes[parent]["parent_id"]
            result.append(dict(artifact_path=str(path),artifact_sha256=digest(path),
                physical_cell_id=match["cell_id"],physical_state=match["status"],
                row_nodes=rownodes,cell_paths=mappings,
                parent_labels=parents,
                path_labels=[nodes[n]["label_raw"] for m in mappings
                             for n in m["row_path"]+m["column_path"] if n in nodes],
                analysis_state=structured["analysis_state"],
                boundaries_unresolved=len(structured["physical_unresolved_boundaries"])))
    return result


def table_context(page, table, ir_hash):
    bbox = table["source"]["bbox"]
    blocks = [b for b in page["blocks"] if b["kind"]=="text"]
    # Text spans are needed because some PDF blocks combine a title, table and footnote.
    snippets = []
    for b in blocks:
        grouped = {}
        for s in b["spans"]:
            grouped.setdefault(s["line_index"],[]).append(s)
        for spans in grouped.values():
            text=" ".join(s["text_raw"] for s in spans)
            boxes=[s["source"]["bbox"] for s in spans]
            box=dict(x0=min(x["x0"] for x in boxes),y0=min(x["y0"] for x in boxes),
                     x1=max(x["x1"] for x in boxes),y1=max(x["y1"] for x in boxes))
            snippets.append(dict(text_raw=text,bbox=box,block_id=b["block_id"],
                                 source=b["source"],span_ids=[s["span_id"] for s in spans]))
    title = [s for s in snippets if s["bbox"]["y1"] <= bbox["y0"]+5
             and bbox["y0"]-s["bbox"]["y1"]<140]
    text = compact(" ".join(s["text_raw"] for s in title))
    # Embedded title/date rows can be part of detector region.
    top_text=compact(" ".join(c["text_raw"] or "" for c in table["cells"] if c["row_index"]<4))
    text += top_text
    family = ("capital_change" if "자본변동" in text else
              "income" if any(x in text for x in ("손익계산서","손익현황")) else
              "balance" if any(x in text for x in ("재무상태표","재무현황")) else "unknown")
    row_labels = [compact(c.get("text_raw")) for c in table["cells"]]
    if family == "unknown" and "손익" in text and all(
        any(term in label for label in row_labels)
        for term in ("영업수익", "영업비용", "법인세", "당기순")
    ):
        family = "income"
    units = [s for s in snippets if "단위" in s["text_raw"]
             and s["bbox"]["y0"] <= bbox["y0"]+65 and s["bbox"]["y0"] >= bbox["y0"]-140]
    notes = [s for s in snippets if re.search(r"^\s*주[):.)]|주석|비고",s["text_raw"])]
    return dict(family=family,title_candidates=title,unit_candidates=units,notes=notes,
                snippet_count=len(snippets)), snippets, blocks
