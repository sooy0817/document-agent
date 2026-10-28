"""Document-local sequence hypotheses. Never consult account names or fixed depths."""

from collections import defaultdict
from .models import RelationCandidate


def evidence_ids(node):
    return node.source_cell_ids or tuple(
        r.span_id or r.block_id for r in node.source_refs if r.span_id or r.block_id
    )


def build_candidates(features, continuations=()):
    candidates = list(continuations)
    groups = defaultdict(list)
    index = {f.node_id: f for f in features}

    def add(parent, child, method, relation="parent_child", depth=None, conflicts=()):
        if parent.node_id == child.node_id:
            return
        candidates.append(
            RelationCandidate(
                candidate_id=f"{method}/{parent.node_id}/{child.node_id}",
                parent_id=parent.node_id,
                child_id=child.node_id,
                relation_type=relation,
                evidence_ids=evidence_ids(parent) + evidence_ids(child),
                methods=(method,),
                conflicts=tuple(conflicts),
                relative_depth=depth,
            )
        )

    for f in features:
        groups[(f.section_id, f.panel_id, f.axis)].append(f)
        if f.physical_parent_id in index:
            add(index[f.physical_parent_id], f, "physical_merge")
    for (_, _, axis), nodes in groups.items():
        if axis == "column":
            continue
        stack = []
        previous = []
        unnumbered = None
        last_detail = None
        date_anchor = None
        heading = None
        for n in sorted(nodes, key=lambda n: n.order):
            role = set(n.role_candidates)
            if "date_anchor" in role:
                date_anchor = n
                stack = []
                unnumbered = n
                previous.append(n)
                continue
            if (
                date_anchor
                and n.table_id
                and n.column_range
                and date_anchor.column_range
                and n.column_range[0] > date_anchor.column_range[0]
            ):
                add(date_anchor, n, "date_group", depth=1)
                previous.append(n)
                continue
            if "paragraph" in role:
                if heading:
                    add(heading, n, "document_section")
                previous.append(n)
                continue
            if "heading_candidate" in role:
                heading = n
            if role & {"alternative_label", "related_adjustment"}:
                if last_detail:
                    add(
                        last_detail,
                        n,
                        "parenthesized_context",
                        "alternative_label"
                        if "alternative_label" in role
                        else "related_adjustment",
                    )
                previous.append(n)
                continue
            bullet = "bullet" in role or n.text_raw.lstrip().startswith(("- ", "• ", "● "))
            if bullet:
                if unnumbered:
                    add(unnumbered, n, "bullet_group", depth=1)
                previous.append(n)
                continue
            if n.number:
                families = [x.number.family for x in stack]
                if n.number.family in families:
                    stack = stack[: families.index(n.number.family)]
                parent = stack[-1] if stack else unnumbered
                if parent:
                    conflicts = []
                    if (
                        n.indent is not None
                        and parent.indent is not None
                        and n.indent + 3 < parent.indent
                    ):
                        conflicts.append("indentation_disagrees")
                    add(parent, n, "number_sequence", depth=len(stack), conflicts=conflicts)
                stack.append(n)
                last_detail = n
            else:
                # Non-numbered group anchors are tentative. Bullets/indentation
                # support their children without a domain label dictionary.
                unnumbered = n
                last_detail = n
                stack = []
            if n.indent is not None:
                shallower = [
                    p
                    for p in previous
                    if p.indent is not None
                    and p.indent + 3 < n.indent
                    and not set(p.role_candidates)
                    & {"alternative_label", "related_adjustment", "paragraph"}
                ]
                if shallower:
                    add(shallower[-1], n, "indentation")
            previous.append(n)
    # Combine independent evidence for an identical edge; retain competing parents.
    merged = {}
    for c in candidates:
        key = (c.parent_id, c.child_id, c.relation_type)
        if key not in merged:
            merged[key] = c
        else:
            old = merged[key]
            merged[key] = old.model_copy(
                update={
                    "methods": tuple(sorted(set(old.methods + c.methods))),
                    "evidence_ids": tuple(sorted(set(old.evidence_ids + c.evidence_ids))),
                    "conflicts": tuple(sorted(set(old.conflicts + c.conflicts))),
                }
            )
    return tuple(merged.values())
