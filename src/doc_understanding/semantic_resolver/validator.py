"""Admissibility is not truth. Review relations never promote physical evidence."""

from collections import defaultdict
from .models import PatchDecision
from .candidates import evidence_ids


def validate_patches(features, candidates, proposals, method="rule", existing=()):
    nodes = {f.node_id: f for f in features}
    edges = {(c.parent_id, c.child_id, c.relation_type): c for c in candidates}
    candidate_parents = defaultdict(set)
    for c in candidates:
        if c.relation_type == "parent_child":
            candidate_parents[c.child_id].add(c.parent_id)
    decisions = []
    graph = {}
    for d in existing:
        if d.validation_status == "accepted" and d.proposal.relation_type == "parent_child":
            graph[d.proposal.child_id] = d.proposal.parent_id
    duplicates = defaultdict(set)
    for p in proposals:
        if p.relation_type == "parent_child":
            duplicates[p.child_id].add(p.parent_id)
    seen = set()
    for p in proposals:
        key = (p.parent_id, p.child_id, p.relation_type)
        if key in seen:
            continue
        seen.add(key)
        reasons = []
        reject = False
        parent = nodes.get(p.parent_id)
        child = nodes.get(p.child_id)
        evidence = set(p.evidence_ids)
        supported = edges.get(key)
        if not parent or not child:
            reasons.append("UNKNOWN_NODE")
            reject = True
        else:
            allowed = set(evidence_ids(parent) + evidence_ids(child))
            if (
                not evidence
                or not evidence <= allowed
                or not evidence.intersection(evidence_ids(parent))
                or not evidence.intersection(evidence_ids(child))
            ):
                reasons.append("MISSING_OR_UNRELATED_EVIDENCE")
                reject = True
            if parent.node_id == child.node_id:
                reasons.append("SELF_CYCLE")
                reject = True
            if parent.panel_id != child.panel_id or parent.axis != child.axis:
                reasons.append("CROSS_PANEL_OR_AXIS")
                reject = True
            if parent.section_id != child.section_id:
                reasons.append("SECTION_SCOPE_CONFLICT")
                reject = True
            if any(
                a.document_id != b.document_id
                for a in parent.source_refs
                for b in child.source_refs
            ):
                reasons.append("CROSS_DOCUMENT")
                reject = True
            if parent.page_number != child.page_number:
                linked_pages = {
                    nodes[c.child_id].page_number
                    for c in candidates
                    if c.relation_type == "continuation"
                    and c.child_id in nodes
                    and c.parent_id in nodes
                    and nodes[c.child_id].section_id == child.section_id
                    and nodes[c.child_id].panel_id == child.panel_id
                    and nodes[c.child_id].page_number == nodes[c.parent_id].page_number + 1
                }
                bridge = (
                    set(
                        range(
                            min(parent.page_number, child.page_number) + 1,
                            max(parent.page_number, child.page_number) + 1,
                        )
                    )
                    <= linked_pages
                )
                if not bridge:
                    reasons.append("UNSUPPORTED_CROSS_PAGE")
                    reject = True
                else:
                    reasons.append("CROSS_PAGE_REVIEW")
            physical = supported and "physical_merge" in supported.methods
            if physical:
                pr, cr = parent.row_range, child.row_range
                if parent.axis == "row" and (
                    not pr or not cr or not (pr[0] <= cr[0] and cr[1] <= pr[1])
                ):
                    reasons.append("PHYSICAL_SPAN_CONFLICT")
                    reject = True
            elif parent.order >= child.order:
                reasons.append("REVERSED_ORDER")
                reject = True
            if p.relation_type == "parent_child":
                # A same-family sibling closes a numbered parent's logical interval.
                if parent.number and any(
                    n.node_id not in (parent.node_id, child.node_id)
                    and n.panel_id == parent.panel_id
                    and n.section_id == parent.section_id
                    and n.number
                    and n.number.family == parent.number.family
                    and parent.order < n.order < child.order
                    for n in features
                ):
                    reasons.append("NUMBERED_SCOPE_ENDED")
                    reject = True
                if len(duplicates[p.child_id]) > 1:
                    reasons.append("MULTIPLE_PROPOSED_PARENTS")
                if p.child_id in graph and graph[p.child_id] != p.parent_id:
                    reasons.append("EXISTING_PARENT_CONFLICT")
                    reject = True
                trail = set()
                cursor = p.parent_id
                while cursor in graph and cursor not in trail:
                    trail.add(cursor)
                    cursor = graph[cursor]
                if cursor == p.child_id or p.child_id in trail:
                    reasons.append("CYCLE")
                    reject = True
            if supported is None:
                reasons.append("NO_RULE_CORROBORATION")
            elif supported.conflicts:
                reasons.extend(supported.conflicts)
            if parent.physical_evidence != "observed" or child.physical_evidence != "observed":
                reasons.append("PHYSICAL_EVIDENCE_REQUIRES_REVIEW")
            if method != "rule":
                reasons.append("MODEL_PROPOSAL_NOT_INDEPENDENTLY_VERIFIED")
            if not physical:
                reasons.append("SEMANTIC_INTERPRETATION_PROVISIONAL")
        status = "rejected" if reject else "review_required" if reasons else "accepted"
        if (
            status != "rejected"
            and p.relation_type == "parent_child"
            and len(duplicates[p.child_id]) == 1
        ):
            graph[p.child_id] = p.parent_id
        decisions.append(
            PatchDecision(
                proposal=p,
                validation_status=status,
                reasons=tuple(dict.fromkeys(reasons)),
                resolution_method=method,
            )
        )
    return tuple(decisions)
