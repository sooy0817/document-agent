import pytest
from pydantic import ValidationError
from tests.test_semantic_resolver_rules import feature
from doc_understanding.agents.semantic_resolver.repair import (
    Registry,
    Operation,
    apply_operations,
    coverage,
    AggregationScope,
    AggregationSidecar,
    validate_aggregation,
)


def setup():
    fs = [feature(i, f"label{i}") for i in range(4)]
    return fs, Registry.create(fs)


def op(reg, **kw):
    return Operation(
        registry_hash=reg.fingerprint,
        child="N003",
        parent="N001",
        operation="ADD",
        reason="mock",
        **kw,
    )


def test_binder_exact_stable_and_no_label_dependence():
    fs, reg = setup()
    assert reg.fingerprint == Registry.create(list(reversed(fs))).fingerprint
    assert reg.bind("N001").node_id == "n0"
    with pytest.raises(ValueError):
        reg.bind("N999")
    changed = [n.model_copy(update={"text_raw": "same"}) for n in fs]
    assert Registry.create(changed).aliases == reg.aliases


def test_add_replace_abstain_and_history():
    fs, reg = setup()
    before = {"n2": "n1"}
    blocked = apply_operations(fs, (), reg, [op(reg)], before)
    assert blocked.after == before
    replacement = op(reg).model_copy(update={"operation": "REPLACE", "expected_parent": "N002"})
    result = apply_operations(fs, (), reg, [replacement], before)
    assert result.after == {"n2": "n0"} and before == {"n2": "n1"}
    abstain = Operation(
        registry_hash=reg.fingerprint, operation="ABSTAIN", child="N003", reason="unclear"
    )
    result = apply_operations(fs, (), reg, [abstain], before)
    assert result.after == before and result.abstained == ("n2",)
    assert coverage(fs, result)["n2"] == "explicit_abstention_prior_preserved"


@pytest.mark.parametrize("kind", ["stale", "unknown", "hash", "multiple", "panel", "self"])
def test_fail_closed(kind):
    fs, reg = setup()
    patch = op(reg)
    before = {}
    if kind == "stale":
        patch = patch.model_copy(update={"operation": "REPLACE", "expected_parent": "N002"})
    if kind == "unknown":
        patch = patch.model_copy(update={"parent": "N999"})
    if kind == "hash":
        patch = patch.model_copy(update={"registry_hash": "bad"})
    if kind == "panel":
        fs[2] = fs[2].model_copy(update={"panel_id": "other"})
        reg = Registry.create(fs)
        patch = patch.model_copy(update={"registry_hash": reg.fingerprint})
    if kind == "self":
        patch = patch.model_copy(update={"parent": "N003"})
    patches = [patch, patch] if kind == "multiple" else [patch]
    result = apply_operations(fs, (), reg, patches, before)
    assert result.after == before
    assert all(d["status"] == "rejected" for d in result.history)


def test_coverage_missing_is_not_root():
    fs, reg = setup()
    result = apply_operations(fs, (), reg, [], {})
    assert set(coverage(fs, result).values()) == {"silent_omission"}
    assert coverage(fs, result, {"n0": ("c0",)})["n0"] == "root_candidate_review_required"
    assert coverage(fs, result, {"n0": ("fake",)})["n0"] == "invalid_root_evidence"


def test_aggregation_separate_and_invalid_scope():
    fs, reg = setup()
    s = AggregationScope(
        scope_id="s1",
        aggregate_id="n3",
        relation="subtotal_of",
        member_ids=("n0", "n1"),
        evidence_ids=("c0", "c1", "c3"),
    )
    side = AggregationSidecar(scopes=(s,))
    assert validate_aggregation(fs, side)[0]["status"] == "review_required"
    bad = s.model_copy(update={"member_ids": ("n3",)})
    assert validate_aggregation(fs, AggregationSidecar(scopes=(bad,)))[0]["status"] == "rejected"
    assert apply_operations(fs, (), reg, [], {}).after == {}


def test_contract_forbids_values_and_invalid_ops():
    _, reg = setup()
    with pytest.raises(ValidationError):
        Operation(registry_hash=reg.fingerprint, operation="ADD", child="N001", reason="x")
    with pytest.raises(ValidationError):
        Operation(
            registry_hash=reg.fingerprint, operation="ABSTAIN", child="N001", reason="x", value=7
        )


@pytest.mark.parametrize(
    "kind", ["duplicate", "unknown", "exclusion", "cross_panel", "cycle", "evidence"]
)
def test_aggregation_negative_cases(kind):
    fs, _ = setup()
    s = AggregationScope(
        scope_id="s1",
        aggregate_id="n3",
        relation="total_of",
        member_ids=("n0", "n1"),
        evidence_ids=("c0", "c1", "c3"),
    )
    scopes = [s]
    if kind == "duplicate":
        scopes.append(s)
    if kind == "unknown":
        scopes = [s.model_copy(update={"member_ids": ("fake",)})]
    if kind == "exclusion":
        scopes = [s.model_copy(update={"excluded_ids": ("n0",)})]
    if kind == "cross_panel":
        fs[0] = fs[0].model_copy(update={"panel_id": "other"})
    if kind == "cycle":
        scopes.append(
            AggregationScope(
                scope_id="s2",
                aggregate_id="n0",
                relation="subtotal_of",
                member_ids=("n3",),
                evidence_ids=("c0", "c3"),
            )
        )
    if kind == "evidence":
        scopes = [s.model_copy(update={"evidence_ids": ("c0",)})]
    assert all(
        d["status"] == "rejected"
        for d in validate_aggregation(fs, AggregationSidecar(scopes=tuple(scopes)))
    )


def test_binding_does_not_make_wrong_semantics_correct():
    fs, reg = setup()
    # Independent synthetic expected parent is n1, but model selects n0.
    result = apply_operations(fs, (), reg, [op(reg)], {})
    assert result.after["n2"] == "n0"
    assert result.after["n2"] != "n1"
    assert result.status == "review_required"


def test_states_preserved_and_registry_tampering_rejected():
    fs, _ = setup()
    fs[0] = fs[0].model_copy(update={"physical_evidence": "unresolved"})
    fs[2] = fs[2].model_copy(update={"physical_evidence": "inferred"})
    reg = Registry.create(fs)
    result = apply_operations(fs, (), reg, [op(reg)], {})
    assert result.physical_states["n0"] == "unresolved"
    assert result.physical_states["n2"] == "inferred"
    payload = reg.model_dump()
    payload["aliases"]["N001"] = "n3"
    with pytest.raises(ValidationError):
        Registry.model_validate(payload)


def test_combined_cycle_rollback():
    from doc_understanding.agents.semantic_resolver.models import RelationCandidate

    fs, _ = setup()
    fs = [n.model_copy(update={"row_range": (0, 5)}) for n in fs]
    reg = Registry.create(fs)
    candidate = RelationCandidate(
        candidate_id="merge",
        parent_id="n2",
        child_id="n0",
        relation_type="parent_child",
        evidence_ids=("c0", "c2"),
        methods=("physical_merge",),
    )
    patch = Operation(
        registry_hash=reg.fingerprint,
        operation="ADD",
        child="N001",
        parent="N003",
        reason="mock cycle",
    )
    result = apply_operations(fs, (candidate,), reg, [patch], {"n2": "n0"})
    assert result.after == result.before
    assert "COMBINED_CYCLE" in result.history[0]["reasons"]


def test_root_conflict_and_rejected_coverage():
    fs, reg = setup()
    result = apply_operations(fs, (), reg, [op(reg).model_copy(update={"parent": "N999"})], {})
    assert coverage(fs, result)["n2"] == "rejected_proposal_unresolved"
    result = apply_operations(fs, (), reg, [op(reg)], {})
    assert coverage(fs, result, {"n2": ("c2",)})["n2"] == "root_parent_conflict"
    with pytest.raises(ValueError):
        coverage(fs, result, {"unknown": ("c0",)})
    assert (
        coverage(fs, result, auxiliary_node_ids={"n3"})["n3"]
        == "auxiliary_only_hierarchy_unresolved"
    )


def test_total_scope_never_mutates_parent_graph():
    fs, reg = setup()
    scope = AggregationScope(
        scope_id="total",
        aggregate_id="n3",
        relation="total_of",
        member_ids=("n0", "n1", "n2"),
        evidence_ids=("c0", "c1", "c2", "c3"),
    )
    assert (
        validate_aggregation(fs, AggregationSidecar(scopes=(scope,)))[0]["status"]
        == "review_required"
    )
    assert apply_operations(fs, (), reg, [], {}).after == {}
