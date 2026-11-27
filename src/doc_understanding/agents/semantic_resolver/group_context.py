"""Opt-in, label-blind group interval proposals. No gold, page or institution rules."""

from collections import defaultdict


def propose_intervals(table, structured):
    """Single-panel sparse headings delimit groups; merged stubs may close them.

    This is a hypothesis generator, not a semantic truth or a subtotal classifier.
    Empty numeric cells can represent missing data, hence all results require review.
    """
    if len(structured["panels"]) != 1:
        return {"proposals": [], "abstentions": ["MULTIPLE_OR_MISSING_PANELS"]}
    panel = structured["panels"][0]
    if panel["stub_columns"] != 1:
        return {"proposals": [], "abstentions": ["MULTI_STUB_UNSUPPORTED"]}
    rows = defaultdict(list)
    for c in table["cells"]:
        rows[c["row_index"]].append(c)
    output = []
    abstentions = []
    active = None
    details = 0
    keys = sorted(r for r in rows if r >= panel["header_rows"])
    for pos, r in enumerate(keys):
        cs = sorted(rows[r], key=lambda c: c["column_index"])
        if not cs or cs[0]["column_index"] != 0:
            active = None
            continue
        stub = cs[0]
        values = [c for c in cs[1:] if (c["original_text"] or "").strip()]
        if not (stub["original_text"] or "").strip():
            active = None
            continue
        # Native-text occupancy, not the spelling/number/year of a label.
        following = rows[keys[pos + 1]] if pos + 1 < len(keys) else []
        next_has_values = any(
            c["column_index"] > 0 and (c["original_text"] or "").strip() for c in following
        )
        if not values:
            active = stub if next_has_values else None
            details = 0
            if not active:
                abstentions.append(
                    {"cell_id": stub["cell_id"], "reason": "NO_FOLLOWING_DETAIL_EVIDENCE"}
                )
            continue
        if active:
            if pos == len(keys) - 1 and stub["colspan"] == 1:
                abstentions.append(
                    {
                        "cell_id": stub["cell_id"],
                        "reason": "OPEN_ENDED_FINAL_ROW_COULD_BE_TOTAL_OR_DETAIL",
                    }
                )
                continue
            output.append(
                {
                    "child_cell_id": stub["cell_id"],
                    "parent_cell_id": active["cell_id"],
                    "evidence_cell_ids": [active["cell_id"], stub["cell_id"]],
                    "reason": "single-panel sparse heading followed by populated rows until next sparse heading or merged-stub closure",
                    "state": "inferred",
                    "status": "review_required",
                    "source_boundary_states": {c["cell_id"]: c["status"] for c in (active, stub)},
                }
            )
            details += 1
            # A merged stub following populated details is an end-of-group candidate.
            # Do not call it subtotal/total without semantic or independent scope evidence.
            if stub["colspan"] > 1 and details > 1:
                abstentions.append(
                    {
                        "cell_id": stub["cell_id"],
                        "reason": "MERGED_STUB_SCOPE_END_CANDIDATE_NOT_VALIDATED_AGGREGATE",
                    }
                )
                active = None
    return {"proposals": output, "abstentions": abstentions}
