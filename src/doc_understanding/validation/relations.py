"""validation / relations."""

from doc_understanding.validation import axes


def merge(roles, relations, known_roles=None):
    role_by_id = dict(known_roles or {})
    role_by_id.update({t["cell_id"]: t["role"] for t in (roles or {}).get("targets", [])})
    targets = []
    for target in relations["targets"]:
        item = {k: v for k, v in target.items() if k != "target_quote"}
        item["role"] = role_by_id.get(target["cell_id"], "unknown")
        for axis in ("row_axis", "column_axis"):
            # Uses the model's unaltered target quote; the validator checks exact source text.
            cites = list(target[axis]["citations"])
            if target["target_quote"]:
                cite = dict(source_id=target["cell_id"], quote=target["target_quote"])
                if cite not in cites:
                    cites.append(cite)
            item[axis] = {**target[axis], "citations": cites}
        targets.append(item)
    return dict(
        summary=relations["summary"],
        targets=targets,
        missing_evidence=(roles or {}).get("missing_evidence", []) + relations["missing_evidence"],
    )


def audit_coverage(packet, interpretation, judgment):
    """Ensure alternatives were assessed; Judge judgments remain fallible semantic candidates."""

    def key(c):
        return c["cell_id"], c["axis"], c["header_id"]

    expected = sorted(key(c) for c in packet["geometric_candidates"])
    actual = sorted(key(c) for c in judgment["coverage"])
    errors = []
    warnings = []
    missing = []
    if actual != expected:
        errors.append("CANDIDATE_COVERAGE_MISMATCH")
    targets = {t["cell_id"]: t for t in interpretation["targets"]}
    sources = {e["id"]: e for e in packet["evidence"]}
    for c in judgment["coverage"]:
        target = targets.get(c["cell_id"])
        path = target[c["axis"] + "_axis"]["header_ids"] if target else []
        if c["requirement"] == "required" and c["header_id"] not in path:
            errors.append("REQUIRED_HEADER_MISSING")
            missing.append(c)
        if c["requirement"] == "not_required" and c["header_id"] in path:
            errors.append("UNRELATED_HEADER_IN_PATH")
        if c["requirement"] == "insufficient_evidence":
            warnings.append("CANDIDATE_SCOPE_UNCERTAIN")
        if not c["reason"].strip() or not c["citations"]:
            errors.append("COVERAGE_REASON_OR_EVIDENCE_MISSING")
        for cite in c["citations"]:
            if (
                cite["source_id"] not in sources
                or not cite["quote"]
                or cite["quote"] not in sources[cite["source_id"]]["text"]
            ):
                errors.append("COVERAGE_QUOTE_NOT_GROUNDED")
        if c["requirement"] == "required" and not {c["cell_id"], c["header_id"]}.issubset(
            {s["source_id"] for s in c["citations"]}
        ):
            errors.append("COVERAGE_ENDPOINT_QUOTES_MISSING")
    return dict(
        errors=sorted(set(errors)), warnings=sorted(set(warnings)), missing_relations=missing
    )


def validate(packet, roles, relations, judgment, index, *, known_roles=None):
    interpretation = merge(roles, relations, known_roles)
    result = axes.validate(packet, interpretation, judgment, index)
    if roles is not None and sorted(t["cell_id"] for t in roles["targets"]) != sorted(packet["target_ids"]):
        result["errors"].append("ROLE_COVERAGE_MISMATCH")
    sources = {e["id"]: e for e in packet["evidence"]}
    for role in (roles or {}).get('targets', []):
        if not role["citations"]:
            result["errors"].append("ROLE_CITATION_MISSING")
        for c in role["citations"]:
            if (
                c["source_id"] not in sources
                or not c["quote"]
                or c["quote"] not in sources[c["source_id"]]["text"]
            ):
                result["errors"].append("ROLE_QUOTE_NOT_GROUNDED")
    for t in relations["targets"]:
        text = sources.get(t["cell_id"], {}).get("text", "")
        if text.strip() and (not t["target_quote"] or t["target_quote"] not in text):
            result["errors"].append("TARGET_QUOTE_NOT_GROUNDED")
    coverage = audit_coverage(packet, interpretation, judgment)
    result["errors"] = sorted(set(result["errors"] + coverage["errors"]))
    result["warnings"] = sorted(set(result["warnings"] + coverage["warnings"]))
    result["missing_relations"] = coverage["missing_relations"]
    result["verdict"] = (
        "held" if result["errors"] or result["warnings"] else "source_supported_candidate"
    )
    return interpretation, result
