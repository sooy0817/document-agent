"""Review diagnostics for saved proposals; never repair values or approve meaning."""

from collections import Counter
from doc_understanding.review.semantic_description.numeric import numeric_checks
from doc_understanding.review.semantic_description.rounding import classify_display_check
from doc_understanding.validation.arithmetic import parse_amount


def diagnose(evidence, proposal):
    ev = {x.evidence_id: x for x in evidence.items}
    nodes = {n.node_id: n for n in proposal.nodes}

    def label(ref):
        n = nodes[ref]
        return n.label_proposed or " ".join(ev[a.evidence_id].text_raw for a in n.anchors)

    bindings = {}
    for b in proposal.bindings:
        if b.row_path and b.column_path:
            bindings.setdefault((b.row_path[-1], b.column_path), []).append(b.cell_evidence_id)
    ops = {x.operation_id: x for x in proposal.operations}
    rows = []
    for result in numeric_checks(evidence, proposal):
        op = ops[result["operation_id"]]
        parts = []
        for ref, coefficient, role in [(o.ref, o.coefficient, "operand") for o in op.operands] + [
            (op.result_ref, 1, "result")
        ]:
            ids = bindings.get((ref, tuple(result["column_path"])), [])
            parts.append(
                dict(
                    node_id=ref,
                    label=label(ref),
                    coefficient=str(coefficient),
                    role=role,
                    evidence_ids=ids,
                    raw_values=[ev[i].text_raw for i in ids],
                )
            )
        reasons = []
        if any(len(x["evidence_ids"]) != 1 for x in parts):
            reasons.append("mapping_missing_or_multiple")
        raw = [v for x in parts for v in x["raw_values"]]
        blanks = sum(not v.strip() for v in raw)
        triangle = any(v.strip().startswith(("△", "▲")) for v in raw)
        if raw and blanks == len(raw):
            reasons.append("all_cells_blank")
        elif blanks:
            reasons.append("some_cells_blank")
        if triangle:
            reasons.append("triangle_sign_policy_missing")
        if any(
            v.strip() and not v.strip().startswith(("△", "▲")) and parse_amount(v) is None
            for v in raw
        ):
            reasons.append("other_unparsed_token")
        if result["status"] == "mismatch":
            reasons.append("displayed_arithmetic_difference")
        primary = (
            "displayed_arithmetic_difference"
            if result["status"] == "mismatch"
            else "mapping_missing_or_multiple"
            if "mapping_missing_or_multiple" in reasons
            else "all_cells_blank"
            if "all_cells_blank" in reasons
            else "blank_and_triangle"
            if blanks and triangle
            else "some_cells_blank"
            if blanks
            else "triangle_sign_policy_missing"
            if triangle
            else "other_unparsed_token"
            if reasons
            else "match"
        )
        assessment = classify_display_check(
            result["status"], raw, [x["coefficient"] for x in parts], result.get("difference")
        )
        bound = assessment["bound"]
        rows.append(
            dict(
                **result,
                result_label=label(op.result_ref),
                column_label=" / ".join(label(n) for n in result["column_path"]),
                components=parts,
                display_assessment=assessment,
                numeric_validation_status={
                    "exact_match": "exact_match",
                    "rounding_compatible": "approximate_match_within_display_rounding",
                    "mismatch": "outside_rounding_envelope",
                    "calculation_unavailable": "calculation_unavailable",
                }[assessment["category"]],
                primary_cause=primary,
                cause_tags=reasons,
                rounding_compatible=None
                if bound is None
                else assessment["category"] == "rounding_compatible",
                rounding_bound=None if bound is None else str(bound),
                source_numeric_error_confirmed=False,
                semantic_structure_error_confirmed=False,
            )
        )
    return dict(
        rows=rows,
        by_status=dict(Counter(x["status"] for x in rows)),
        primary_causes=dict(Counter(x["primary_cause"] for x in rows if x["status"] != "match")),
        counting_unit="one proposed operation evaluated in one column",
        semantic_accuracy=None,
        source_values_modified=False,
    )
