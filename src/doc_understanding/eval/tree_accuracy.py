"""Source-anchored tree diagnostics. No PDF-specific hierarchy rules here."""

import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def norm(value):
    return "".join((value or "").split())


def iou(a, b):
    def area(r):
        return max(0, r[2] - r[0]) * max(0, r[3] - r[1])
    intersection = area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])
    return intersection / max(area(a) + area(b) - intersection, 1e-10)


def box(d):
    return [d[k] for k in ("x0", "y0", "x1", "y1")]


def path(node, parents):
    result, seen = [], set()
    while node is not None:
        if node in seen:
            return ["CYCLE"] + result
        seen.add(node)
        result.insert(0, node)
        node = parents.get(node)
    return result


def fraction(correct, total):
    return {"correct": correct, "total": total, "value": correct / total if total else None}


def evaluate_table(gold, physical, structured, semantic_parents):
    """Evaluate complete annotated row domain; unmatched IDs cannot become correct roots."""
    cells = physical["cells"]
    binding, issues = {}, []
    for g in gold["cells"]:
        candidates = sorted(
            ((iou(g["bbox"], box(p["source_bbox"])), p) for p in cells),
            key=lambda x: x[0],
            reverse=True,
        )
        top = candidates[0] if candidates else (0, None)
        ambiguous = len(candidates) > 1 and candidates[1][0] >= 0.7
        if top[0] >= 0.7 and not ambiguous:
            binding[g["id"]] = top[1]["cell_id"]
            if norm(top[1]["original_text"]) != norm(g["text"]):
                issues.append(
                    {
                        "stage": "Physical Grid Reconstruction",
                        "code": "TEXT_MISMATCH",
                        "target": g["id"],
                        "expected": g["text"],
                        "predicted": top[1]["original_text"],
                    }
                )
        else:
            issues.append(
                {
                    "stage": "Physical Grid Reconstruction",
                    "code": "CELL_UNBOUND",
                    "target": g["id"],
                    "expected": g["bbox"],
                    "predicted": None if top[1] is None else box(top[1]["source_bbox"]),
                }
            )
    inverse = {v: k for k, v in binding.items()}
    # Reject a many-to-one source binding, never silently overwrite.
    if len(inverse) != len(binding):
        duplicates = {v for v in binding.values() if list(binding.values()).count(v) > 1}
        for k, v in list(binding.items()):
            if v in duplicates:
                issues.append(
                    {
                        "stage": "ID Binding / Patch Application",
                        "code": "AMBIGUOUS_BINDING",
                        "target": k,
                        "expected": k,
                        "predicted": v,
                    }
                )
                del binding[k]
        inverse = {v: k for k, v in binding.items()}
    nodes = {n["node_id"]: n for axis in ("row_tree", "column_tree") for n in structured[axis]}
    maps = {m["cell_id"]: m for m in structured["cell_mappings"]}
    expected = {r["cell"]: r["parent"] for r in gold["rows"]}
    pred = {}
    for row in gold["rows"]:
        cid = binding.get(row["cell"])
        p = semantic_parents.get(cid)
        pred[row["cell"]] = inverse.get(p, "UNBOUND_PARENT") if p else None
        if not cid:
            pred[row["cell"]] = "UNBOUND_CHILD"
    targets = set(expected)
    ge = {(c, p) for c, p in expected.items() if p is not None}
    pe = {(c, p) for c, p in pred.items() if p is not None}
    tp = len(ge & pe)
    row_exact = 0
    for child in targets:
        ep, pp = path(child, expected), path(child, pred)
        row_exact += ep == pp
        if ep != pp:
            issues.append(
                {
                    "stage": "Semantic Resolver",
                    "code": "ROW_PATH",
                    "target": child,
                    "expected": ep,
                    "predicted": pp,
                }
            )
    col_ok = 0
    header_paths = {h["leaf"]: h["path"] for h in gold["columns"]}
    for leaf, ep in header_paths.items():
        matching = [
            n for n in structured["column_tree"] if n["source_cell_id"] == binding.get(leaf)
        ]
        pp = []
        if len(matching) == 1:
            node_path = path(
                matching[0]["node_id"],
                {n["node_id"]: n["parent_id"] for n in structured["column_tree"]},
            )
            pp = [
                inverse.get(nodes[n]["source_cell_id"], "UNBOUND_HEADER") if n in nodes else n
                for n in node_path
            ]
        col_ok += ep == pp
        if ep != pp:
            issues.append(
                {
                    "stage": "Row/Column Tree",
                    "code": "COLUMN_PATH",
                    "target": leaf,
                    "expected": ep,
                    "predicted": pp,
                }
            )
    map_ok = evidence_ok = 0
    for m in gold["mappings"]:
        cid = binding.get(m["cell"])
        pm = maps.get(cid)
        er, ec = m["row_path"], m["column_path"]
        pr, pc = [], []
        if pm:
            pc = [
                inverse.get(nodes[n]["source_cell_id"], "UNBOUND_HEADER")
                if n in nodes
                else "MISSING_NODE"
                for n in pm["column_path"]
            ]
            physical_row = [
                inverse.get(nodes[n]["source_cell_id"], "UNBOUND_ROW")
                if n in nodes
                else "MISSING_NODE"
                for n in pm["row_path"]
            ]
            if physical_row:
                pr = path(physical_row[-1], pred)
            else:
                pr = []
        map_ok += pr == er and pc == ec
        if pr != er or pc != ec:
            stage = (
                "Semantic Resolver"
                if pc == ec and pr and er and pr[-1] == er[-1]
                else "Row/Column Tree"
            )
            issues.append(
                {
                    "stage": stage,
                    "code": "CELL_PATH",
                    "severity": "high",
                    "target": m["cell"],
                    "expected": {"row": er, "column": ec},
                    "predicted": {"row": pr, "column": pc},
                    "raw_value": m["text"],
                }
            )
        gc = next(g for g in gold["cells"] if g["id"] == m["cell"])
        physical_cell = next((c for c in cells if c["cell_id"] == cid), None)
        good = bool(
            pm
            and physical_cell
            and norm(pm.get("original_text")) == norm(gc["text"])
            and iou(box(pm["bbox"]), gc["bbox"]) >= 0.7
            and physical_cell["source"]["page_number"] == gold["page"]
        )
        evidence_ok += good
        if not good:
            issues.append(
                {
                    "stage": "Physical Grid Reconstruction" if not cid else "Row/Column Tree",
                    "code": "EVIDENCE",
                    "target": m["cell"],
                    "expected": gc["text"],
                    "predicted": pm.get("original_text") if pm else None,
                }
            )
    return {
        "table_id": gold["id"],
        "page": gold["page"],
        "bindings": binding,
        "metrics": {
            "parent_precision": fraction(tp, len(pe)),
            "parent_recall": fraction(tp, len(ge)),
            "exact_row_path": fraction(row_exact, len(targets)),
            "column_path": fraction(col_ok, len(header_paths)),
            "cell_to_path": fraction(map_ok, len(gold["mappings"])),
            "evidence_accuracy": fraction(evidence_ok, len(gold["mappings"])),
            "physical_binding_coverage": fraction(len(binding), len(gold["cells"])),
        },
        "issues": issues,
        "predicted_row_parents": pred,
        "expected_row_parents": expected,
    }


def micro(tables):
    names = tables[0]["metrics"] if tables else []
    return {
        name: fraction(
            sum(t["metrics"][name]["correct"] for t in tables),
            sum(t["metrics"][name]["total"] for t in tables),
        )
        for name in names
    }
