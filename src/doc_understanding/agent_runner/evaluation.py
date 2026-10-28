"""Source-bound evaluation adapter. No reviewed gold => no accuracy."""

from .adapter import digest


def evaluate(canonical, descriptions, gold=None, gold_descriptions=None, review=None):
    metrics = {
        k: None
        for k in (
            "parent_precision",
            "parent_recall",
            "parent_edge_set_exact",
            "cell_mapping_exact",
            "operation_scope_exact",
            "description_assertions_exact",
            "semantic_prose_accuracy",
        )
    }
    result = dict(
        metrics=metrics,
        status="not_evaluated",
        gold_status="missing",
        mock=descriptions.origin == "mock",
        generalization_claim=False,
    )
    if not gold or not review or review.get("status") != "independent_reviewed":
        return result
    if (
        review.get("canonical_sha256") != gold.digest()
        or review.get("descriptions_sha256")
        != (gold_descriptions.digest() if gold_descriptions else None)
        or not review.get("reviewer")
        or canonical.evidence.items != gold.evidence.items
        or canonical.evidence.artifacts != gold.evidence.artifacts
    ):
        raise ValueError("EVALUATION_SOURCE_OR_GOLD_MISMATCH")

    def signatures(p):
        nodes = {
            n.node_id: (n.kind, n.axis, tuple((a.evidence_id, a.text_range) for a in n.anchors))
            for n in p.nodes
        }
        parents = {
            (nodes[r.source], nodes[r.targets[0]]) for r in p.relations if r.type == "CHILD_OF"
        }
        bindings = {
            (
                b.cell_evidence_id,
                tuple(nodes[n] for n in b.row_path),
                tuple(nodes[n] for n in b.column_path),
            )
            for b in p.bindings
        }

        # Exact operations/scopes use canonical anchored IDs, never run-specific IDs.
        def replace(value):
            if isinstance(value, str):
                return nodes.get(value, value)
            if isinstance(value, list):
                return [replace(v) for v in value]
            if isinstance(value, dict):
                return {
                    k: replace(v) for k, v in value.items() if k not in ("operation_id", "claim_id")
                }
            return value

        ops = {o.operation_id: replace(o.model_dump(mode="json")) for o in p.operations}
        scope = sorted(
            digest(
                dict(
                    type=r.type,
                    source=nodes[r.source],
                    targets=[nodes[t] for t in r.targets],
                    scope=replace(r.scope.model_dump(mode="json")),
                    operation=ops.get(r.operation_id),
                )
            )
            for r in p.relations
            if r.type in ("AGGREGATES", "DERIVED_FROM")
        )
        return parents, bindings, scope

    pp, pb, po = signatures(canonical.proposal)
    gp, gb, go = signatures(gold.proposal)
    metrics.update(
        parent_precision=len(pp & gp) / len(pp) if pp else None,
        parent_recall=len(pp & gp) / len(gp) if gp else None,
        parent_edge_set_exact=pp == gp,
        cell_mapping_exact=pb == gb,
        operation_scope_exact=po == go,
    )
    result.update(status="evaluated_annotated_scope", gold_status="independent_reviewed")
    # Prose and unsupported metrics intentionally remain N/A, not proxy scores.
    return result
