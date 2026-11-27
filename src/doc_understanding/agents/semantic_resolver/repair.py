"""Offline, opt-in repair contracts. Provenance binding is not semantic verification."""

import hashlib
from collections import Counter
from typing import Literal
from pydantic import model_validator
from doc_understanding.domain.models import EvidenceModel
from .models import SemanticFeature, SemanticPatch
from .candidates import evidence_ids
from .validator import validate_patches


class Operation(EvidenceModel):
    schema_version: Literal["semantic-operation/1.0"] = "semantic-operation/1.0"
    registry_hash: str
    operation: Literal["ADD", "REPLACE", "ABSTAIN"]
    child: str
    parent: str | None = None
    expected_parent: str | None = None
    reason: str

    @model_validator(mode="after")
    def shape(self):
        if self.operation == "ABSTAIN":
            if self.parent is not None or self.expected_parent is not None:
                raise ValueError("ABSTAIN cannot mutate a parent")
        elif not self.parent:
            raise ValueError("parent required")
        if self.operation == "REPLACE" and not self.expected_parent:
            raise ValueError("REPLACE requires old parent")
        if self.operation == "ADD" and self.expected_parent is not None:
            raise ValueError("ADD has no old parent")
        return self


class Registry(EvidenceModel):
    schema_version: Literal["semantic-id-registry/1.0"] = "semantic-id-registry/1.0"
    features: tuple[SemanticFeature, ...]
    aliases: dict[str, str]
    fingerprint: str

    @model_validator(mode="after")
    def integrity(self):
        fs = tuple(sorted(self.features, key=lambda n: n.node_id))
        expected = {f"N{i:03d}": n.node_id for i, n in enumerate(fs, 1)}
        digest = hashlib.sha256("\n".join(n.model_dump_json() for n in fs).encode()).hexdigest()
        if (
            len({n.node_id for n in fs}) != len(fs)
            or self.aliases != expected
            or self.fingerprint != digest
        ):
            raise ValueError("invalid registry")
        return self

    @classmethod
    def create(cls, features):
        fs = tuple(sorted(features, key=lambda n: n.node_id))
        if len({n.node_id for n in fs}) != len(fs):
            raise ValueError("duplicate original node ID")
        aliases = {f"N{i:03d}": n.node_id for i, n in enumerate(fs, 1)}
        fingerprint = hashlib.sha256(
            "\n".join(n.model_dump_json() for n in fs).encode()
        ).hexdigest()
        return cls(features=fs, aliases=aliases, fingerprint=fingerprint)

    def bind(self, alias):
        if alias not in self.aliases:
            raise ValueError("UNKNOWN_SHORT_ID")
        return next(n for n in self.features if n.node_id == self.aliases[alias])

    def payload(self):
        """No original IDs or numeric value cells exposed to future/mock chooser."""
        return {
            "registry_hash": self.fingerprint,
            "nodes": [
                {
                    "id": a,
                    "label": self.bind(a).text_raw,
                    "order": self.bind(a).order,
                    "row_range": self.bind(a).row_range,
                    "column_range": self.bind(a).column_range,
                    "physical_evidence": self.bind(a).physical_evidence,
                }
                for a in self.aliases
            ],
        }


class Revision(EvidenceModel):
    schema_version: Literal["semantic-revision/1.0"] = "semantic-revision/1.0"
    before: dict[str, str]
    after: dict[str, str]
    history: tuple[dict, ...]
    abstained: tuple[str, ...]
    physical_states: dict[str, str]
    status: Literal["review_required"] = "review_required"


def _cycle(graph):
    for start in graph:
        seen = set()
        cursor = start
        while cursor in graph:
            if cursor in seen:
                return True
            seen.add(cursor)
            cursor = graph[cursor]
    return False


def apply_operations(features, candidates, registry, operations, before):
    """Stage changes; reject conflicting children and cycles, retain original revision."""
    if Registry.create(features).fingerprint != registry.fingerprint:
        raise ValueError("registry source mismatch")
    ids = {f.node_id for f in features}
    if not set(before) <= ids or not set(before.values()) <= ids or _cycle(before):
        raise ValueError("invalid baseline graph")
    graph = dict(before)
    history, abstained, staged = [], [], []
    duplicates = Counter(p.child for p in operations)
    for p in operations:
        record = {"operation": p.model_dump(mode="json"), "status": "rejected", "reasons": []}
        history.append(record)
        try:
            if p.registry_hash != registry.fingerprint:
                raise ValueError("REGISTRY_HASH_MISMATCH")
            child = registry.bind(p.child)
            record["child_id"] = child.node_id
            if duplicates[p.child] > 1:
                raise ValueError("MULTIPLE_OPERATIONS_FOR_CHILD")
            if p.operation == "ABSTAIN":
                abstained.append(child.node_id)
                record.update(status="review_required", reasons=["EXPLICIT_ABSTENTION"])
                continue
            parent = registry.bind(p.parent)
            old = registry.bind(p.expected_parent).node_id if p.expected_parent else None
            if p.operation == "ADD" and child.node_id in before:
                raise ValueError("ADD_WOULD_OVERWRITE")
            if p.operation == "REPLACE" and before.get(child.node_id) != old:
                raise ValueError("STALE_REPLACE")
            patch = SemanticPatch(
                parent_id=parent.node_id,
                child_id=child.node_id,
                relation_type="parent_child",
                reason=p.reason,
                evidence_ids=list(dict.fromkeys(evidence_ids(parent) + evidence_ids(child))),
            )
            d = validate_patches(features, candidates, [patch], "mock")[0]
            record.update(
                bound_patch=patch.model_dump(mode="json"),
                reasons=list(d.reasons),
                evidence_origin="deterministic_source_binding",
            )
            if d.validation_status == "rejected":
                continue
            graph[child.node_id] = parent.node_id
            record.update(status="review_required")
            staged.append((child.node_id, record))
        except ValueError as error:
            record["reasons"] = [str(error)]
    # Atomic rollback of staged mutations if combined graph has a cycle.
    if _cycle(graph):
        graph = dict(before)
        for _, record in staged:
            record.update(status="rejected", reasons=record["reasons"] + ["COMBINED_CYCLE"])
    return Revision(
        before=dict(before),
        after=graph,
        history=tuple(history),
        abstained=tuple(abstained),
        physical_states={n.node_id: n.physical_evidence for n in features},
    )


def coverage(features, revision, root_evidence=None, auxiliary_node_ids=()):
    """No parent != root. Even evidence-anchored root declarations need review."""
    roots = root_evidence or {}
    if not set(roots) <= {f.node_id for f in features}:
        raise ValueError("UNKNOWN_ROOT_NODE")
    result = {}
    touched = {r.get("child_id") for r in revision.history}
    for node in features:
        nid = node.node_id
        if nid in revision.abstained:
            state = (
                "explicit_abstention_prior_preserved"
                if nid in revision.after
                else "explicit_abstention"
            )
        elif nid in roots:
            valid = bool(roots[nid]) and set(roots[nid]) <= set(evidence_ids(node))
            state = "root_candidate_review_required" if valid else "invalid_root_evidence"
            if nid in revision.after:
                state = "root_parent_conflict"
        elif nid in revision.after:
            state = "parent_candidate_review_required"
        elif nid in touched:
            state = "rejected_proposal_unresolved"
        elif nid in auxiliary_node_ids:
            state = "auxiliary_only_hierarchy_unresolved"
        else:
            state = "silent_omission"
        result[nid] = state
    return result


class CoverageReport(EvidenceModel):
    schema_version: Literal["semantic-coverage/1.0"] = "semantic-coverage/1.0"
    node_states: dict[
        str,
        Literal[
            "explicit_abstention_prior_preserved",
            "explicit_abstention",
            "root_candidate_review_required",
            "invalid_root_evidence",
            "root_parent_conflict",
            "parent_candidate_review_required",
            "rejected_proposal_unresolved",
            "silent_omission",
            "auxiliary_only_hierarchy_unresolved",
        ],
    ]
    independently_validated_roots: Literal[False] = False


class AggregationScope(EvidenceModel):
    scope_id: str
    aggregate_id: str
    relation: Literal["subtotal_of", "total_of"]
    member_ids: tuple[str, ...]
    excluded_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...]
    status: Literal["proposed", "review_required"] = "proposed"


class AggregationSidecar(EvidenceModel):
    schema_version: Literal["aggregation-sidecar/1.0"] = "aggregation-sidecar/1.0"
    scopes: tuple[AggregationScope, ...] = ()
    scope_type: Literal["aggregation_scope"] = "aggregation_scope"
    arithmetic_validated: Literal[False] = False


def validate_aggregation(features, sidecar):
    nodes = {f.node_id: f for f in features}
    counts = Counter(s.scope_id for s in sidecar.scopes)
    aggregate_counts = Counter(s.aggregate_id for s in sidecar.scopes)
    graph = {s.aggregate_id: set(s.member_ids) for s in sidecar.scopes}

    def cyclic(start, node, seen):
        if node == start:
            return True
        if node in seen:
            return False
        return any(cyclic(start, c, seen | {node}) for c in graph.get(node, ()))

    output = []
    for scope in sidecar.scopes:
        issues = []
        ids = (scope.aggregate_id,) + scope.member_ids + scope.excluded_ids
        if counts[scope.scope_id] > 1 or aggregate_counts[scope.aggregate_id] > 1:
            issues.append("DUPLICATE_OR_AMBIGUOUS_SCOPE")
        if not scope.member_ids or len(set(scope.member_ids)) != len(scope.member_ids):
            issues.append("EMPTY_OR_DUPLICATE_MEMBERS")
        if set(scope.member_ids) & set(scope.excluded_ids):
            issues.append("MEMBER_EXCLUSION_CONFLICT")
        if not set(ids) <= nodes.keys():
            issues.append("UNKNOWN_NODE")
        else:
            contexts = {
                (
                    nodes[i].section_id,
                    nodes[i].panel_id,
                    nodes[i].axis,
                    nodes[i].page_number,
                    frozenset(r.document_id for r in nodes[i].source_refs),
                )
                for i in ids
            }
            if len(contexts) != 1:
                issues.append("UNSUPPORTED_SCOPE_CROSSING")
            allowed = set(e for i in ids for e in evidence_ids(nodes[i]))
            if not set(scope.evidence_ids) <= allowed or any(
                not set(scope.evidence_ids).intersection(evidence_ids(nodes[i])) for i in ids
            ):
                issues.append("MISSING_OR_UNRELATED_EVIDENCE")
        if any(cyclic(scope.aggregate_id, c, set()) for c in scope.member_ids):
            issues.append("AGGREGATION_CYCLE")
        output.append(
            {
                "scope_id": scope.scope_id,
                "status": "rejected" if issues else "review_required",
                "issues": issues,
                "semantic_validated": False,
                "physical_states": {i: nodes[i].physical_evidence for i in ids if i in nodes},
            }
        )
    return output
