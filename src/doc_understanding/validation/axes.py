"""validation / axes."""

from doc_understanding.tools import focused_evidence


def relation_check(packet, relation):
    """Necessary evidence/direction checks only; geometry cannot prove semantic correctness."""
    sources = {e["id"]: e for e in packet["evidence"]}
    a = sources.get(relation.get("source_id"))
    b = sources.get(relation.get("target_id"))
    errors = []
    warnings = []
    if not a or not b:
        return dict(errors=["ENDPOINT_OUTSIDE_FOCUSED_EVIDENCE"], warnings=[])
    kind = relation.get("relation")
    if a["id"] == b["id"]:
        errors.append("SELF_RELATION")
    if kind in ("row_header", "column_header"):
        if a["kind"] != "cell" or b["kind"] != "cell":
            errors.append("AXIS_ENDPOINT_NOT_CELL")
        else:
            ab, bb = a["bbox"], b["bbox"]
            if a["page"] == b["page"]:
                left = ab["x1"] <= bb["x0"] + 1.5 and focused_evidence.overlap(ab, bb, "y") >= 0.5
                above = ab["y1"] <= bb["y0"] + 1.5 and focused_evidence.overlap(ab, bb, "x") >= 0.5
                if (kind == "row_header" and above and not left) or (
                    kind == "column_header" and left and not above
                ):
                    errors.append("AXIS_DIRECTION_CONFLICT")
            if a.get("table_id") != b.get("table_id"):
                warnings.append("FRAGMENT_CONTINUITY_REQUIRES_REVIEW")
    if kind == "parent_table" and (
        a["kind"] != "cell" or b["kind"] != "table" or a.get("table_id") == b["id"]
    ):
        errors.append("INVALID_NESTED_RELATION_ENDPOINTS")
    cites = relation.get("citations", [])
    if not cites:
        errors.append("CITATION_MISSING")
    for c in cites:
        src = sources.get(c["source_id"])
        if not src or not c["quote"] or c["quote"] not in src["text"]:
            errors.append("QUOTE_NOT_GROUNDED")
    if kind in ("row_header", "column_header") and not {a["id"], b["id"]}.issubset(
        {c["source_id"] for c in cites}
    ):
        errors.append("BOTH_ENDPOINT_QUOTES_REQUIRED")
    return dict(errors=sorted(set(errors)), warnings=warnings)


def relations(interpretation):
    result = []
    for t in interpretation["targets"]:
        for axis in ("row", "column"):
            for sid in t[axis + "_axis"]["header_ids"]:
                result.append(
                    dict(
                        relation=axis + "_header",
                        source_id=sid,
                        target_id=t["cell_id"],
                        citations=t[axis + "_axis"]["citations"],
                    )
                )
    return result


def validate(packet, interpretation, judgment, index):
    errors = []
    warnings = []
    targets = interpretation["targets"]
    expected = packet["target_ids"]
    if sorted(t["cell_id"] for t in targets) != sorted(expected):
        errors.append("TARGET_COVERAGE_MISMATCH")
    wanted = sorted((cid, axis) for cid in expected for axis in ("row", "column"))
    if sorted((d["cell_id"], d["axis"]) for d in judgment["decisions"]) != wanted:
        errors.append("JUDGE_AXIS_COVERAGE_MISMATCH")
    sources = {e["id"]: e for e in packet["evidence"]}
    for t in targets:
        if t["role"] == "unknown":
            warnings.append("TARGET_ROLE_UNKNOWN")
        if set(t["row_axis"]["header_ids"]) & set(t["column_axis"]["header_ids"]):
            errors.append("HEADER_USED_ON_BOTH_AXES")
        for axis in ("row_axis", "column_axis"):
            a = t[axis]
            if len(a["header_ids"]) != len(set(a["header_ids"])):
                errors.append("DUPLICATE_PATH_NODE")
            if a["status"] in ("explicit", "contextual_candidate") and not a["header_ids"]:
                errors.append("HEADER_PATH_EMPTY")
            if a["status"] in ("no_header", "unknown") and a["header_ids"]:
                errors.append("ABSENT_HEADER_HAS_PATH")
            if a["status"] == "no_header" and (not a["reason"].strip() or not a["citations"]):
                errors.append("NO_HEADER_EVIDENCE_MISSING")
            if a["status"] in ("unknown", "contextual_candidate"):
                warnings.append("AXIS_NEEDS_HUMAN_REVIEW")
        if any(sid not in sources for sid in t["context_ids"]):
            errors.append("UNKNOWN_CONTEXT_ID")
    for obj in [a for t in targets for a in (t["row_axis"], t["column_axis"])] + judgment[
        "decisions"
    ]:
        for cite in obj["citations"]:
            if (
                cite["source_id"] not in sources
                or not cite["quote"]
                or cite["quote"] not in sources[cite["source_id"]]["text"]
            ):
                errors.append("QUOTE_NOT_GROUNDED")
    for d in judgment["decisions"]:
        if d["verdict"] == "supported" and not d["citations"]:
            errors.append("JUDGE_SUPPORT_WITHOUT_CITATION")
        target = next((t for t in targets if t["cell_id"] == d["cell_id"]), None)
        if d["verdict"] == "not_applicable" and target and target["role"] == "value":
            errors.append("VALUE_AXIS_MARKED_NOT_APPLICABLE")
        if d["verdict"] in ("contradicted", "insufficient_evidence"):
            warnings.append("JUDGE_NOT_SUPPORTED")
    lookups = []
    for r in relations(interpretation):
        check = relation_check(packet, r)
        errors += check["errors"]
        warnings += check["warnings"]
        lookups.append(index.lookup(r["relation"], r["source_id"], r["target_id"]))
    if (
        interpretation["missing_evidence"]
        or judgment["missing_evidence"]
        or packet["omitted_ids"]
        or packet["unreviewed_target_ids"]
    ):
        warnings.append("INCOMPLETE_REVIEW")
    return dict(
        errors=sorted(set(errors)),
        warnings=sorted(set(warnings)),
        storage_checks=lookups,
        semantic_verified=False,
        training_ready=False,
        verdict="held" if errors or warnings else "source_supported_candidate",
    )
