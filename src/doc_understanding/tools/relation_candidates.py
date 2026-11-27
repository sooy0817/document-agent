"""tools / relation_candidates."""

from doc_understanding.tools import focused_evidence


def geometric_candidates(packet):
    """Visible neighbouring cells to consider, not accepted semantic parents."""
    cells = {e["id"]: e for e in packet["evidence"] if e["kind"] == "cell"}
    candidates = []
    for tid in packet["target_ids"]:
        target = cells.get(tid)
        if not target:
            continue
        b = target["bbox"]
        for sid, source in cells.items():
            if sid == tid or source["page"] != target["page"] or not source["text"].strip():
                continue
            a = source["bbox"]
            if a["x1"] <= b["x0"] + 1.5 and focused_evidence.overlap(a, b, "y") >= 0.5:
                candidates.append(dict(cell_id=tid, axis="row", header_id=sid))
            if a["y1"] <= b["y0"] + 1.5 and focused_evidence.overlap(a, b, "x") >= 0.5:
                candidates.append(dict(cell_id=tid, axis="column", header_id=sid))
    return sorted(candidates, key=lambda c: (c["cell_id"], c["axis"], c["header_id"]))


def with_candidates(packet):
    candidates = geometric_candidates(packet)
    # Never silently leave alternatives unexamined and then approve a whole target.
    if len(candidates) > 160:
        raise ValueError("RELATION_CANDIDATE_BUDGET_EXCEEDED")
    return {**packet, "geometric_candidates": candidates}
