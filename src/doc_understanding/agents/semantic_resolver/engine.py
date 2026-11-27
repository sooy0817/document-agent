"""Immutable overlay assembly and bounded comparison routes."""

import hashlib
from collections import defaultdict
from uuid import uuid4
from .models import (
    CellSnapshot,
    ResolverConfig,
    ResolverRequest,
    SemanticOverlay,
    SemanticNode,
    SemanticPatch,
)
from .features import extract_features
from .candidates import build_candidates, evidence_ids
from .validator import validate_patches
from .providers import token_upper_bound


def digest(model):
    return hashlib.sha256(model.model_dump_json().encode()).hexdigest()


def make_requests(document, features, candidates, hashes, mode, config):
    groups = defaultdict(list)
    for f in features:
        if f.axis in ("row", "document"):
            groups[(f.section_id, f.panel_id)].append(f)
    requests = []
    physical_clear = {
        c.child_id for c in candidates if c.methods == ("physical_merge",) and not c.conflicts
    }
    for (section, panel), nodes in groups.items():
        # Limit each request to a local section window and its candidate ancestors.
        nodes = sorted(nodes, key=lambda n: n.order)
        for offset in range(0, len(nodes), max(1, config.max_nodes_per_request // 2)):
            target = nodes[offset : offset + max(1, config.max_nodes_per_request // 2)]
            if mode == "hybrid":
                target = [n for n in target if n.node_id not in physical_clear]
            if not target:
                continue
            wanted = {n.node_id for n in target}
            for _ in range(6):
                parents = {c.parent_id for c in candidates if c.child_id in wanted}
                if parents <= wanted:
                    break
                wanted |= parents
            wanted |= {n.node_id for n in nodes[max(0, offset - 2) : offset]}
            scope = tuple(n for n in features if n.node_id in wanted)
            if len(scope) > config.max_nodes_per_request:
                continue
            local = tuple(c for c in candidates if c.parent_id in wanted and c.child_id in wanted)
            above = [
                f
                for f in features
                if f.axis == "document"
                and "heading_candidate" in f.role_candidates
                and f.page_number <= target[0].page_number
            ]
            req = ResolverRequest(
                request_id=f"{document.document_id}/request{len(requests)}",
                document_id=document.document_id,
                mode=mode,
                input_hashes=hashes,
                nodes=scope,
                candidates=local if mode == "hybrid" else (),
                section_context=tuple(n.text_raw for n in above[-2:]),
                continuation_candidates=tuple(
                    c for c in local if c.relation_type == "continuation"
                ),
                target_node_ids=tuple(n.node_id for n in target),
                allowed_node_ids=tuple(n.node_id for n in scope),
                allowed_evidence_ids=tuple(sorted({i for n in scope for i in evidence_ids(n)})),
            )
            if token_upper_bound(req, config) <= config.max_input_tokens:
                requests.append(req)
    return tuple(requests)


def assemble_nodes(features, decisions, run, model):
    by_child = defaultdict(list)
    for d in decisions:
        if d.validation_status != "rejected":
            by_child[d.proposal.child_id].append(d)
    chosen = {}
    for child, ds in by_child.items():
        model_choices = [
            d
            for d in ds
            if d.resolution_method in ("mock", "text_llm")
            and d.proposal.relation_type == "parent_child"
        ]
        model_parents = {d.proposal.parent_id for d in model_choices}
        if len(model_parents) == 1:
            chosen[child] = model_choices[-1]
            continue
        parents = {d.proposal.parent_id for d in ds if d.proposal.relation_type == "parent_child"}
        if len(parents) == 1:
            chosen[child] = next(
                d for d in reversed(ds) if d.proposal.relation_type == "parent_child"
            )
    output = []
    for f in features:
        d = chosen.get(f.node_id)
        other = by_child[f.node_id]
        if d is None and len(other) == 1:
            d = other[0]
        path = [f.node_id]
        cursor = f.node_id
        seen = {cursor}
        if d and d.proposal.relation_type not in ("parent_child", "continuation"):
            cursor = d.proposal.parent_id
            path.append(cursor)
            seen.add(cursor)
        while cursor in chosen:
            cursor = chosen[cursor].proposal.parent_id
            if cursor in seen:
                break
            path.append(cursor)
            seen.add(cursor)
        unresolved = (
            None
            if d and d.validation_status == "accepted"
            else ";".join(d.reasons)
            if d
            else "ROOT_OR_PARENT_UNRESOLVED"
        )
        output.append(
            SemanticNode(
                semantic_node_id=f.node_id,
                source_node_id=f.source_node_id,
                source_cell_ids=f.source_cell_ids,
                physical_parent_id=f.physical_parent_id,
                semantic_parent_id=d.proposal.parent_id if d else None,
                relation_type=d.proposal.relation_type if d else None,
                hierarchy_path=tuple(reversed(path)),
                semantic_role=f.role_candidates[0] if f.role_candidates else f.axis,
                evidence=f.source_refs,
                resolution_method=d.resolution_method if d else "none",
                validation_status=d.validation_status if d else "review_required",
                unresolved_reason=unresolved,
                model_id=model,
                run_id=run,
                candidate_parent_ids=tuple(sorted({x.proposal.parent_id for x in other})),
            )
        )
    return tuple(output)


def resolve_document(
    document,
    physical,
    structured,
    mode="rule_only",
    provider=None,
    config=None,
    accounting_scope=None,
):
    config = config or ResolverConfig()
    run = uuid4()
    hashes = {
        "document": digest(document),
        **{f"physical:{t.table_id}": digest(t) for t in physical},
        **{f"structured:{t.table_id}": digest(t) for t in structured},
    }
    features, continuations = extract_features(document, physical, structured)
    candidates = build_candidates(features, continuations)
    proposals = [
        SemanticPatch(
            parent_id=c.parent_id,
            child_id=c.child_id,
            relation_type=c.relation_type,
            evidence_ids=list(c.evidence_ids),
            reason=";".join(c.methods),
        )
        for c in candidates
    ]
    decisions = (
        list(validate_patches(features, candidates, proposals)) if mode != "llm_only" else []
    )
    requests = (
        make_requests(document, features, candidates, hashes, mode, config)
        if mode != "rule_only"
        else ()
    )
    calls = []
    escalations = []
    for page in document.pages:
        if page.text_status != "native" or sum(len(b.text_raw or "") for b in page.blocks) < 20:
            escalations.append(
                f"page:{page.page_number}:requires_ocr_or_vision:insufficient_native_text"
            )
    reserved = 0
    for req in requests:
        if provider is None:
            break
        needed = (token_upper_bound(req, config) + config.max_output_tokens) * (1 + config.retries)
        if len(calls) >= config.max_calls or reserved + needed > config.total_token_budget:
            escalations.append("remaining_requests_deferred_by_budget")
            break
        reserved += needed
        try:
            proposal, record = provider.resolve(req, config)
        except ValueError as exc:
            escalations.append(str(exc))
            continue
        calls.append(record)
        # Enforce the local allowlist before validating against document context.
        scoped = []
        for p in proposal.patches:
            if (
                p.parent_id not in req.allowed_node_ids
                or p.child_id not in req.allowed_node_ids
                or not set(p.evidence_ids) <= set(req.allowed_evidence_ids)
            ):
                from .models import PatchDecision

                decisions.append(
                    PatchDecision(
                        proposal=p,
                        validation_status="rejected",
                        reasons=("REQUEST_ALLOWLIST_VIOLATION",),
                        resolution_method="mock" if provider.name == "mock" else "text_llm",
                    )
                )
            else:
                scoped.append(p)
        decisions.extend(
            validate_patches(
                features,
                candidates,
                scoped,
                "mock" if provider.name == "mock" else "text_llm",
                tuple(decisions),
            )
        )
    model = (
        config.model_id if provider and provider.name != "mock" else "mock" if provider else None
    )
    nodes = assemble_nodes(features, decisions, run, model)
    snapshots = tuple(
        CellSnapshot(
            cell_id=c.cell_id,
            text_raw=c.original_text,
            bbox=c.source_bbox,
            physical_status=c.status,
        )
        for t in physical
        for c in t.cells
    )
    overlay = SemanticOverlay(
        document_id=document.document_id,
        accounting_scope=accounting_scope or document.metadata.accounting_scope,
        scope_provenance="caller_supplied" if accounting_scope else document.metadata.provenance,
        run_id=run,
        mode=mode,
        model_id=model,
        input_hashes=hashes,
        features=features,
        candidates=candidates,
        decisions=tuple(decisions),
        table_nodes=tuple(
            n
            for n in nodes
            if next(f.axis for f in features if f.node_id == n.semantic_node_id) != "document"
        ),
        document_nodes=tuple(
            n
            for n in nodes
            if next(f.axis for f in features if f.node_id == n.semantic_node_id) == "document"
        ),
        source_cells=snapshots,
        calls=tuple(calls),
        escalations=tuple(escalations),
        status="requires_ocr_or_vision" if not features and escalations else "provisional",
    )
    assert hashes["document"] == digest(document)
    assert all(hashes[f"physical:{t.table_id}"] == digest(t) for t in physical)
    assert all(hashes[f"structured:{t.table_id}"] == digest(t) for t in structured)
    return overlay, requests
