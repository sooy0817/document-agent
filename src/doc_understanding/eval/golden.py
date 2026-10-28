"""Geometry-only evaluation on explicit, versioned annotations. Never train from predictions."""

import json
from pathlib import Path


def xy(box):
    return [box[k] for k in ("x0", "y0", "x1", "y1")] if isinstance(box, dict) else box


def iou(a, b):
    a, b = xy(a), xy(b)
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1])
    )
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union else 0


def matches(predicted, expected, score, threshold):
    candidates = sorted(
        ((score(p, g), i, j) for i, p in enumerate(predicted) for j, g in enumerate(expected)),
        reverse=True,
    )
    ps, gs, pairs = set(), set(), []
    for value, i, j in candidates:
        if value >= threshold and i not in ps and j not in gs:
            pairs.append((i, j))
            ps.add(i)
            gs.add(j)
    return pairs


def fraction(n, d):
    return n / d if d else None


def evaluate(folder: Path, golden: dict):
    pages = golden["complete_detection_pages"]
    pred = []
    missing = []
    for p in pages:
        path = folder / f"page-{p:03d}.json"
        if not path.exists():
            missing.append(p)
        else:
            pred.extend(json.loads(path.read_text(encoding="utf-8"))["tables"])
    if missing:
        return {"status": "incomplete_prediction_pages", "missing_pages": missing, "metrics": None}
    gold = golden["tables"]
    pairs = matches(
        pred,
        gold,
        lambda p, g: iou(p["bounding_box"], g["bbox"]) if p["page_number"] == g["page"] else 0,
        0.8,
    )
    tol = golden["provenance"]["coordinate_tolerance_points"]
    cell_total = sum(len(t["cells"]) for t in gold)
    edge_total = sum(len(t["boundaries"]) for t in gold)
    merge_total = sum(c["rowspan"] > 1 or c["colspan"] > 1 for t in gold for c in t["cells"])
    cp = bp = mp = resolved_cp = observed_bp = observed_mp = 0
    cell_predictions = sum(len(t["cells"]) for t in pred)
    edges_pred = sum(
        sum(b["status"] in ("present", "inferred") for b in t["boundaries"]) for t in pred
    )
    observed_edges_pred = sum(sum(b["status"] == "present" for b in t["boundaries"]) for t in pred)
    failures = []
    relations = []
    for pi, gi in pairs:
        p, g = pred[pi], gold[gi]
        cells = matches(
            p["cells"],
            g["cells"],
            lambda a, b: float(
                max(abs(x - y) for x, y in zip(xy(a["source_bbox"]), b["bbox"])) <= tol
            ),
            1,
        )
        cp += len(cells)
        resolved_cp += sum(p["cells"][i]["status"] == "resolved" for i, j in cells)
        for i, j in cells:
            pc, gc = p["cells"][i], g["cells"][j]
            if gc["rowspan"] > 1 or gc["colspan"] > 1:
                correct = int(
                    pc["rowspan"] == gc["rowspan"]
                    and pc["colspan"] == gc["colspan"]
                    and pc["occupied_slots"] == gc["occupied_slots"]
                )
                mp += correct
                observed_mp += correct * (pc["status"] == "resolved")
        edges = [b for b in p["boundaries"] if b["status"] in ("present", "inferred")]
        em = matches(
            edges,
            g["boundaries"],
            lambda a, b: float(
                a["axis"] == b["axis"]
                and all(abs(a[k] - b[k]) <= tol for k in ("position", "start", "end"))
            ),
            1,
        )
        bp += len(em)
        observed_bp += sum(edges[i]["status"] == "present" for i, j in em)
        failures.append(
            dict(
                table=g["id"],
                gold_cells=len(g["cells"]),
                matched_geometry=len(cells),
                mismatched_gold_cell_ids=[
                    c["id"] for j, c in enumerate(g["cells"]) if j not in {y for x, y in cells}
                ],
                unresolved=sum(c["status"] == "unresolved" for c in p["cells"]),
            )
        )
        mapping = {j: i for i, j in cells}
        for rel in golden.get("focused_relations", []):
            if rel["table"] != g["id"]:
                continue
            ids = []
            for key in ("container", "member"):
                ids.append(
                    next(
                        (
                            j
                            for j, c in enumerate(g["cells"])
                            if [c["row"], c["column"]] == rel[key]
                        ),
                        None,
                    )
                )
            pcells = [p["cells"][mapping[j]] if j in mapping else None for j in ids]
            valid = all(c and c["rowspan"] is not None for c in pcells)
            if valid:
                a, b = pcells
                ar = set(r for r, c in a["occupied_slots"])
                br = set(r for r, c in b["occupied_slots"])
                valid = br <= ar if rel["expected"] == "row_range_contains" else not ar & br
            relations.append(dict(label=rel["label"], passed=bool(valid)))
    false_regions = [
        dict(page=t["page_number"], bbox=t["bounding_box"])
        for i, t in enumerate(pred)
        if i not in {i for i, j in pairs}
    ]
    negatives = []
    for region in golden.get("negative_regions", []):
        path = folder / f"page-{region['page']:03d}.json"
        if not path.exists():
            negatives.append(dict(page=region["page"], evaluated=False, count=None))
            continue
        tables = json.loads(path.read_text(encoding="utf-8"))["tables"]
        hits = [t for t in tables if iou(t["bounding_box"], region["bbox"]) > 0]
        negatives.append(dict(page=region["page"], evaluated=True, count=len(hits)))
    return dict(
        scope_pages=pages,
        annotation_review=golden["provenance"]["review_status"],
        detection_precision=fraction(len(pairs), len(pred)),
        detection_recall=fraction(len(pairs), len(gold)),
        boundary_precision=fraction(bp, edges_pred),
        boundary_recall=fraction(bp, edge_total),
        cell_geometry_accuracy=fraction(cp, cell_total),
        cell_geometry_precision=fraction(cp, cell_predictions),
        resolved_cell_geometry_accuracy=fraction(resolved_cp, cell_total),
        merged_span_accuracy=fraction(mp, merge_total),
        observed_only_merged_span_accuracy=fraction(observed_mp, merge_total),
        observed_only_boundary_precision=fraction(observed_bp, observed_edges_pred),
        observed_only_boundary_recall=fraction(observed_bp, edge_total),
        unresolved_rate=fraction(
            sum(c["status"] == "unresolved" for t in pred for c in t["cells"]), cell_predictions
        ),
        inferred_cell_rate=fraction(
            sum(c["status"] == "inferred" for t in pred for c in t["cells"]), cell_predictions
        ),
        review_required_cell_rate=fraction(
            sum(c["status"] != "resolved" for t in pred for c in t["cells"]), cell_predictions
        ),
        boundary_metric_scope="physical boundaries: observed + explicitly inferred; not pixel stroke accuracy",
        denominators=dict(
            predicted_regions=len(pred),
            gold_regions=len(gold),
            gold_cells=cell_total,
            predicted_cells=cell_predictions,
            gold_boundaries=edge_total,
            predicted_boundaries=edges_pred,
            gold_merged_cells=merge_total,
        ),
        false_positive_regions=false_regions,
        negative_region_false_positives=negatives,
        table_details=failures,
        focused_relations=relations,
        whole_document_precision=None,
        whole_document_recall=None,
        whole_document_na_reason="No complete 38-page detection gold; selected negative regions are separate.",
    )
