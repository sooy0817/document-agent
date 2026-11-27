"""validation / review."""


def check_outputs(packet, analysis, judge):
    """Necessary ID/quote/coverage checks, not proof of semantic entailment."""
    errors = []
    sources = {e["id"]: e for e in packet["evidence"]}
    if analysis is None or judge is None:
        return ["MODEL_OUTPUT_UNAVAILABLE"]
    issues = analysis["issues"]
    assessments = judge["assessments"]
    ids = [i["issue_id"] for i in issues]
    if len(ids) != len(set(ids)):
        errors.append("DUPLICATE_ISSUE_ID")
    if sorted(a["issue_id"] for a in assessments) != sorted(ids):
        errors.append("JUDGE_COVERAGE_MISMATCH")
    for obj in issues + assessments:
        cites = obj["citations"]
        if not cites:
            errors.append("CITATION_MISSING")
        for c in cites:
            src = sources.get(c["source_id"])
            if src is None:
                errors.append("UNKNOWN_SOURCE_ID")
            elif c["quote"] and c["quote"] not in src["text"]:
                errors.append("QUOTE_MISMATCH")
        for sid in obj.get("target_ids", []):
            if sid not in sources:
                errors.append("UNKNOWN_TARGET_ID")
        if "target_ids" in obj and not obj["target_ids"]:
            errors.append("TARGET_MISSING")
        if obj.get("relation") == "role":
            if obj["source_id"] not in sources:
                errors.append("ROLE_SOURCE_MISSING")
        elif "relation" in obj and obj["relation"] != "none":
            a, b = obj["source_id"], obj["target_id"]
            if a not in sources or b not in sources:
                errors.append("RELATION_SOURCE_MISSING")
            elif a == b:
                errors.append("SELF_RELATION")
            elif obj["relation"] in ("row_header", "column_header") and sources[a].get(
                "table_id"
            ) != sources[b].get("table_id"):
                errors.append("CROSS_TABLE_AXIS_RELATION")
    edges = {}
    for i in issues:
        if (
            i["relation"] in ("parent_table", "row_header", "column_header")
            and i["source_id"]
            and i["target_id"]
        ):
            edges.setdefault(i["source_id"], set()).add(i["target_id"])
    for start in edges:
        pending = list(edges[start])
        visited = set()
        while pending:
            current = pending.pop()
            if current == start:
                errors.append("RELATION_CYCLE")
                break
            if current not in visited:
                visited.add(current)
                pending.extend(edges.get(current, ()))
    return sorted(set(errors))
