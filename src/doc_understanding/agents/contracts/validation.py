"""Deterministic contract admissibility checks, not a semantic truth oracle."""

from .models import Check, ValidationResult


def validate_bundle(evidence, proposal):
    checks = []

    def check(dim, code, ok, ids=()):
        checks.append(
            Check(dimension=dim, code=code, outcome="pass" if ok else "fail", target_ids=tuple(ids))
        )

    ev = {x.evidence_id: x for x in evidence.items}
    nodes = {x.node_id: x for x in proposal.nodes}
    claims = {x.claim_id: x for x in proposal.relations}
    ops = {x.operation_id: x for x in proposal.operations}
    refs = set(ev) | set(nodes) | set(ops)
    check("source", "EVIDENCE_HASH_MISMATCH", evidence.digest() == proposal.evidence_sha256)
    check("source", "MODE_MATCH", evidence.mode == proposal.mode)
    check("source", "ID_NAMESPACE_COLLISION", not set(ev).intersection(nodes.keys() | ops.keys()))
    for n in nodes.values():
        fragments = []
        for a in n.anchors:
            check("source", "UNKNOWN_EVIDENCE", a.evidence_id in ev, [n.node_id])
            if a.evidence_id not in ev:
                continue
            text = ev[a.evidence_id].text_raw
            if a.text_range is not None:
                start, end = a.text_range
                valid = text is not None and 0 <= start < end <= len(text)
                check("source", "INVALID_TEXT_RANGE", valid, [n.node_id])
                if valid:
                    fragments.append(text[start:end])
            elif text is not None:
                fragments.append(text)
        if n.label_origin == "source" and n.label_proposed is not None:
            check("source", "SOURCE_LABEL_MATCH", n.label_proposed in fragments, [n.node_id])
        if n.panel_id:
            check(
                "structure",
                "PANEL_REFERENCE",
                n.panel_id in nodes and nodes[n.panel_id].kind == "panel",
                [n.node_id],
            )
    graphs = {}
    parents = {}
    for c in claims.values():
        check(
            "structure",
            "RELATION_ENDPOINT",
            c.source in nodes and all(t in nodes for t in c.targets),
            [c.claim_id],
        )
        check(
            "source",
            "RELATION_EVIDENCE",
            all(x in ev for x in c.evidence_ids + c.counterevidence_ids),
            [c.claim_id],
        )
        check(
            "conflict",
            "CONFLICT_REFERENCE",
            all(x in claims and x != c.claim_id for x in c.conflicts_with),
            [c.claim_id],
        )
        check(
            "scope",
            "SCOPE_REFERENCE",
            all(x in refs for x in c.scope.included + c.scope.excluded),
            [c.claim_id],
        )
        check(
            "scope",
            "SCOPE_OVERLAP",
            not set(c.scope.included).intersection(c.scope.excluded),
            [c.claim_id],
        )
        for q in c.scope.qualifiers:
            check(
                "source", "QUALIFIER_EVIDENCE", all(x in ev for x in q.evidence_ids), [c.claim_id]
            )
        if c.source in nodes and all(t in nodes for t in c.targets):
            source = nodes[c.source]
            if c.type == "CHILD_OF":
                check(
                    "structure",
                    "HIERARCHY_AXIS",
                    all(not ({source.kind, nodes[t].kind} == {"row", "column"}) for t in c.targets),
                    [c.claim_id],
                )
            if c.type == "APPLIES_TO":
                check(
                    "structure",
                    "APPLIES_SOURCE_KIND",
                    source.kind in ("context", "formula", "record"),
                    [c.claim_id],
                )
            if c.type == "CONTINUES_AS":
                check(
                    "structure",
                    "CONTINUATION_KIND",
                    all(nodes[t].kind == source.kind for t in c.targets),
                    [c.claim_id],
                )
        if c.operation_id:
            check("operation", "OPERATION_REFERENCE", c.operation_id in ops, [c.claim_id])
            if c.operation_id in ops and c.type in ("AGGREGATES", "DERIVED_FROM"):
                op = ops[c.operation_id]
                check(
                    "operation",
                    "RELATION_OPERATION_MATCH",
                    op.result_ref == c.source and set(c.targets) == {o.ref for o in op.operands},
                    [c.claim_id],
                )
        if c.type in ("CHILD_OF", "CONTINUES_AS") and not c.alternative_set_id:
            graphs.setdefault((c.type, c.layer), {}).setdefault(c.source, []).extend(c.targets)
        if c.type == "CHILD_OF":
            parents.setdefault((c.source, c.layer), []).append(c)
    for group in parents.values():
        if len(group) > 1:
            check(
                "conflict",
                "MULTIPLE_PARENTS",
                bool(group[0].alternative_set_id)
                and len({c.alternative_set_id for c in group}) == 1,
                [c.claim_id for c in group],
            )

    def acyclic(graph):
        active, done = set(), set()

        def visit(n):
            if n in active:
                return False
            if n in done:
                return True
            active.add(n)
            if not all(visit(t) for t in graph.get(n, [])):
                return False
            active.remove(n)
            done.add(n)
            return True

        return all(visit(n) for n in graph)

    for graph in graphs.values():
        check("structure", "RELATION_CYCLE", acyclic(graph))
    opgraph = {}
    for op in ops.values():
        check(
            "operation",
            "OPERAND_REFERENCE",
            all(o.ref in refs for o in op.operands)
            and (op.result_ref is None or op.result_ref in refs),
            [op.operation_id],
        )
        check(
            "source",
            "FORMULA_EVIDENCE",
            all(x in ev for x in op.formula_evidence_ids),
            [op.operation_id],
        )
        opgraph[op.operation_id] = [o.ref for o in op.operands if o.ref in ops]
    check("operation", "OPERATION_CYCLE", acyclic(opgraph))
    bound = {}
    for b in proposal.bindings:
        check(
            "binding",
            "UNKNOWN_CELL",
            b.cell_evidence_id in ev and ev[b.cell_evidence_id].kind == "cell",
            [b.binding_id],
        )
        bound.setdefault(b.cell_evidence_id, []).append(b)
        for axis, path in (("row", b.row_path), ("column", b.column_path)):
            check(
                "binding",
                "PATH_NODE_TYPE",
                all(
                    x in nodes
                    and nodes[x].kind in (axis, "group", "item", "record")
                    and nodes[x].axis in (axis, "none")
                    for x in path
                ),
                [b.binding_id],
            )
            check("binding", "PATH_DUPLICATE", len(path) == len(set(path)), [b.binding_id])
            for parent, child in zip(path, path[1:]):
                check(
                    "binding",
                    "PATH_EDGE",
                    any(
                        c.type == "CHILD_OF" and c.source == child and parent in c.targets
                        for c in claims.values()
                    ),
                    [b.binding_id],
                )
        panels = {
            nodes[x].panel_id
            for x in b.row_path + b.column_path
            if x in nodes and nodes[x].panel_id
        }
        check("binding", "PANEL_CROSSING", len(panels) <= 1, [b.binding_id])
        for q in b.qualifiers:
            check(
                "source", "QUALIFIER_EVIDENCE", all(x in ev for x in q.evidence_ids), [b.binding_id]
            )
    for bs in bound.values():
        check(
            "binding",
            "DUPLICATE_BINDING",
            len(bs) == 1
            or (bool(bs[0].alternative_set_id) and len({b.alternative_set_id for b in bs}) == 1),
            [b.binding_id for b in bs],
        )
    for tree in proposal.trees:
        check(
            "structure",
            "TREE_REFERENCE",
            all(n in nodes for n in tree.node_ids)
            and set(tree.root_candidate_ids) <= set(tree.node_ids),
            [tree.tree_id],
        )
        edges = [claims[x] for x in tree.edge_claim_ids if x in claims]
        check(
            "structure",
            "TREE_EDGES",
            len(edges) == len(tree.edge_claim_ids)
            and all(
                c.type == "CHILD_OF"
                and c.source in tree.node_ids
                and set(c.targets) <= set(tree.node_ids)
                for c in edges
            ),
            [tree.tree_id],
        )
        graph = {}
        for c in edges:
            graph.setdefault(c.source, []).extend(c.targets)
        check("structure", "TREE_CYCLE", acyclic(graph), [tree.tree_id])
        check(
            "structure",
            "TREE_ROOT_PARENT",
            not set(tree.root_candidate_ids).intersection(graph),
            [tree.tree_id],
        )
    for a in proposal.ambiguities:
        check(
            "source",
            "AMBIGUITY_REFERENCE",
            all(x in ev for x in a.review_evidence_ids)
            and all(x in claims for x in a.candidate_claim_ids)
            and all(x in refs or x in claims for x in a.targets),
            [a.ambiguity_id],
        )
    coverage = [x.evidence_id for x in proposal.coverage]
    check("coverage", "COVERAGE_MISSING", set(coverage) == set(ev))
    check("coverage", "COVERAGE_DUPLICATE", len(coverage) == len(set(coverage)))
    checks.extend(
        Check(dimension=d, code=c, outcome="not_evaluated")
        for d, c in [
            ("source", "ARTIFACT_BYTES_NOT_CHECKED"),
            ("operation", "ARITHMETIC_NOT_EXECUTED"),
            ("semantic", "MEANING_NOT_ESTABLISHED"),
            ("human_review", "INDEPENDENT_REVIEW_PENDING"),
        ]
    )
    return ValidationResult(
        evidence_sha256=evidence.digest(), proposal_sha256=proposal.digest(), checks=checks
    )


def validate_projection(canonical, projection):
    """Reject dangling retrieval references; prose truth requires separate review."""
    if canonical.digest() != projection.canonical_sha256:
        return False
    ev = {x.evidence_id: x for x in canonical.evidence.items}
    p = canonical.proposal
    nodes = {x.node_id for x in p.nodes}
    claims = {x.claim_id for x in p.relations}
    bindings = {x.binding_id for x in p.bindings}
    artifacts = {x.artifact_id for x in canonical.evidence.artifacts}
    for u in projection.units:
        if (
            not set(u.binding_ids) <= bindings
            or not set(u.relation_ids) <= claims
            or not set(u.source_artifact_ids) <= artifacts
        ):
            return False
        if any(not set(path) <= nodes for path in u.node_paths):
            return False
        texts = (u.summary, u.schema_narration) + u.content_signature
        for t in texts:
            if not set(t.evidence_ids) <= ev.keys() or not set(t.claim_ids) <= claims:
                return False
            if t.status == "source_transcription" and t.text not in [
                ev[x].text_raw for x in t.evidence_ids
            ]:
                return False
        if projection.policy == "source_only" and (
            u.node_paths
            or u.relation_ids
            or u.binding_ids
            or any(t.claim_ids or t.status != "source_transcription" for t in texts)
        ):
            return False
    return True


def validate_comparison(independent, assisted, plan):
    """Check paired input equality without running or scoring either interpreter."""
    import hashlib
    import json

    source = {
        "artifacts": [a.model_dump() for a in independent.artifacts],
        "items": [i.model_dump() for i in independent.items],
    }
    digest = hashlib.sha256(
        json.dumps(source, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return (
        independent.mode == "independent"
        and assisted.mode == "candidate_assisted"
        and independent.artifacts == assisted.artifacts
        and independent.items == assisted.items
        and independent.available_context == assisted.available_context
        and independent.missing_context == assisted.missing_context
        and independent.excluded_source_ids == assisted.excluded_source_ids
        and independent.digest() == plan.independent_bundle_sha256
        and assisted.digest() == plan.assisted_bundle_sha256
        and digest == plan.source_evidence_digest
        and tuple(a.sha256 for a in assisted.candidates) == plan.candidate_artifact_hashes
    )
