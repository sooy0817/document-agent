import pytest
from pydantic import ValidationError
from tests.test_semantic_resolver_rules import feature
from doc_understanding.agents.semantic_resolver.context import (
    build_context,
    ContextResponse,
    RootClaim,
    ShortAggregation,
    validate_response,
)
from doc_understanding.agents.semantic_resolver.repair import Operation
from doc_understanding.domain.extraction_envelope import ExtractionEnvelope, StoredArtifact


def setup():
    fs = [feature(i, "Group" if i == 0 else "Item") for i in range(4)]
    reg, req = build_context("synthetic", fs, (), (), {"n2": "n1"})
    return reg, req


def test_context_retains_layout_and_baseline_without_long_ids():
    reg, req = setup()
    assert req.current_parents == {"N003": "N002"}
    assert req.nodes[1]["previous"] == "N001"
    assert req.nodes[0]["indent"] == 0
    assert req.nodes[0]["physical_evidence"] == "observed"
    assert req.limitations
    assert "source_cell_ids" not in req.model_dump_json()
    assert reg.bind("N001").text_raw == "Group"


def test_replace_and_scope_bind_through_existing_validator():
    reg, req = setup()
    op = Operation(
        registry_hash=reg.fingerprint,
        operation="REPLACE",
        child="N003",
        parent="N001",
        expected_parent="N002",
        reason="mock group",
    )
    response = ContextResponse(
        registry_hash=reg.fingerprint,
        context_digest=req.context_digest,
        operations=(op,),
        aggregations=(
            ShortAggregation(
                scope_id="s1",
                aggregate="N004",
                relation="subtotal_of",
                members=("N002", "N003"),
                reason="mock scope",
            ),
        ),
    )
    result = validate_response(req, reg, (), response, {"n2": "n1"})
    assert result["revision"]["after"] == {"n2": "n0"}
    assert result["aggregation_decisions"][0]["status"] == "review_required"
    assert result["aggregation"]["scopes"][0]["member_ids"] == ["n1", "n2"]
    assert result["missing_dispositions"] == ["N001", "N002", "N004"]


def test_root_does_not_delete_existing_parent():
    reg, req = setup()
    response = ContextResponse(
        registry_hash=reg.fingerprint,
        context_digest=req.context_digest,
        roots=(RootClaim(node="N003", support_nodes=("N001",), reason="mock root"),),
    )
    result = validate_response(req, reg, (), response, {"n2": "n1"})
    assert result["revision"]["after"] == {"n2": "n1"}
    assert result["coverage"]["n2"] == "root_parent_conflict"


@pytest.mark.parametrize("error", ["digest", "baseline", "dispositions", "unknown_scope"])
def test_context_fail_closed(error):
    reg, req = setup()
    response = ContextResponse(registry_hash=reg.fingerprint, context_digest=req.context_digest)
    before = {"n2": "n1"}
    if error == "digest":
        response = response.model_copy(update={"context_digest": "old"})
    if error == "baseline":
        before = {}
    if error == "dispositions":
        response = response.model_copy(
            update={"roots": (RootClaim(node="N001", support_nodes=("N001",), reason="x"),) * 2}
        )
    if error == "unknown_scope":
        response = response.model_copy(
            update={
                "aggregations": (
                    ShortAggregation(
                        scope_id="s",
                        aggregate="N999",
                        relation="total_of",
                        members=("N001",),
                        reason="x",
                    ),
                )
            }
        )
        assert (
            validate_response(req, reg, (), response, before)["aggregation_decisions"][0]["status"]
            == "rejected"
        )
    else:
        with pytest.raises(ValueError):
            validate_response(req, reg, (), response, before)


def test_envelope_failed_runs_and_no_facts():
    kwargs = dict(
        source_document_id="d",
        source_version_sha256="a" * 64,
        source_uri="local.pdf",
        parsing_run_id="r",
        parsing_run_status="failed",
        configuration={},
        failures=({"stage": "physical"},),
    )
    assert not ExtractionEnvelope(**kwargs).fact_publication_allowed
    with pytest.raises(ValidationError):
        ExtractionEnvelope(**kwargs, financial_facts=({"value": 1},))
    a = StoredArtifact(
        artifact_id="a",
        kind="Trace",
        schema_version="x",
        original_uri="x.json",
        raw_sha256="b" * 64,
        payload={},
        validation_status="invalid",
        upstream_artifact_ids=("missing",),
    )
    with pytest.raises(ValidationError):
        ExtractionEnvelope(**kwargs, artifacts=(a,))


def test_physical_occupancy_and_numbers_not_rewritten_or_sent():
    from tests.semantic_fixtures import compound

    table = compound("laboratory")[0]
    label = table.cells[0]
    cells = tuple(
        c if c == label else c.model_copy(update={"original_text": "987654321.23"})
        for c in table.cells
    )
    table = table.model_copy(update={"cells": cells})
    f = feature(0, label.original_text).model_copy(
        update={"source_cell_ids": (label.cell_id,), "table_id": table.table_id}
    )
    original = table.model_dump_json()
    _, req = build_context("test", (f,), (table,), (), {})
    assert req.nodes[0]["physical_cells"][0]["occupied_slots"] == label.occupied_slots
    assert "987654321.23" not in req.model_dump_json()
    assert table.model_dump_json() == original


def test_tampered_context_rejected():
    reg, req = setup()
    response = ContextResponse(registry_hash=reg.fingerprint, context_digest=req.context_digest)
    changed = req.model_copy(update={"limitations": ("changed",)})
    with pytest.raises(ValueError, match="INTEGRITY"):
        validate_response(changed, reg, (), response, {"n2": "n1"})
