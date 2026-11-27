"""Contract tests only; synthetic labels do not encode NH document rules."""

import pytest
from pydantic import ValidationError

from doc_understanding.agents.contracts.models import (
    EvidenceBundle,
    StructureProposalBundle,
    Operation,
    RAGProjection,
)
from doc_understanding.agents.contracts.validation import validate_bundle


def fixture():
    evidence = EvidenceBundle.model_validate(
        {
            "bundle_id": "synthetic",
            "mode": "independent",
            "artifacts": [
                {
                    "artifact_id": "a",
                    "kind": "physical",
                    "uri": "fixture.json",
                    "sha256": "a" * 64,
                    "document_sha256": "b" * 64,
                }
            ],
            "items": [
                {
                    "evidence_id": k,
                    "artifact_id": "a",
                    "source_id": k,
                    "kind": "cell",
                    "page": 1,
                    "bbox": [0, i * 10, 30, i * 10 + 10],
                    "text_raw": text,
                }
                for i, (k, text) in enumerate(
                    [("c1", "Alpha; Beta"), ("c2", "Measure"), ("c3", "12")]
                )
            ],
        }
    )
    proposal = StructureProposalBundle.model_validate(
        {
            "proposal_id": "p",
            "evidence_sha256": evidence.digest(),
            "mode": "independent",
            "origin": "synthetic_fixture",
            "nodes": [
                {
                    "node_id": "r",
                    "kind": "row",
                    "anchors": [{"evidence_id": "c1", "text_range": [0, 5]}],
                },
                {
                    "node_id": "r2",
                    "kind": "row",
                    "anchors": [{"evidence_id": "c1", "text_range": [7, 11]}],
                },
                {"node_id": "h", "kind": "column", "anchors": [{"evidence_id": "c2"}]},
            ],
            "bindings": [
                {
                    "binding_id": "b",
                    "cell_evidence_id": "c3",
                    "row_path": ["r"],
                    "column_path": ["h"],
                    "measure_role": "reported_value",
                }
            ],
            "coverage": [
                {"evidence_id": k, "disposition": "role_only", "reason": "fixture"}
                for k in ("c1", "c2", "c3")
            ],
        }
    )
    return evidence, proposal


def test_multi_logical_items_one_cell_is_admissible_not_gold():
    e, p = fixture()
    result = validate_bundle(e, p)
    assert result.admissible
    assert result.semantic_truth == "not_established"
    assert len(p.nodes) == 3
    assert e == EvidenceBundle.model_validate_json(e.model_dump_json())


def test_independent_forbids_rule_artifacts():
    e, _ = fixture()
    data = e.model_dump()
    data["candidates"] = [
        {
            "artifact_id": "rule",
            "kind": "structured",
            "uri": "rule.json",
            "sha256": "c" * 64,
            "document_sha256": "b" * 64,
        }
    ]
    with pytest.raises(ValidationError):
        EvidenceBundle.model_validate(data)


@pytest.mark.parametrize(
    "change,code",
    [
        ("unknown_anchor", "UNKNOWN_EVIDENCE"),
        ("bad_range", "INVALID_TEXT_RANGE"),
        ("unknown_cell", "UNKNOWN_CELL"),
        ("stale", "EVIDENCE_HASH_MISMATCH"),
        ("omission", "COVERAGE_MISSING"),
    ],
)
def test_negative_reference_cases(change, code):
    e, p = fixture()
    data = p.model_dump()
    if change == "unknown_anchor":
        data["nodes"][0]["anchors"][0]["evidence_id"] = "invented"
    if change == "bad_range":
        data["nodes"][0]["anchors"][0]["text_range"] = [0, 999]
    if change == "unknown_cell":
        data["bindings"][0]["cell_evidence_id"] = "invented"
    if change == "stale":
        data["evidence_sha256"] = "f" * 64
    if change == "omission":
        data["coverage"] = []
    r = validate_bundle(e, StructureProposalBundle.model_validate(data))
    assert code in [c.code for c in r.checks if c.outcome == "fail"]
    assert not r.admissible


def test_operation_arity_and_symbolic_missing_values():
    with pytest.raises(ValidationError):
        Operation(operation_id="op", operator="divide", operands=[])
    op = Operation(
        operation_id="op",
        operator="sum",
        operands=[{"ref": "r", "role": "input"}],
        value_state="symbolic",
    )
    assert op.value_state == "symbolic"


def test_relation_cycles_and_alternative_parents():
    e, p = fixture()
    data = p.model_dump()
    data["relations"] = [
        {
            "claim_id": "a",
            "type": "CHILD_OF",
            "source": "r",
            "targets": ["r2"],
            "evidence_ids": ["c1"],
        },
        {
            "claim_id": "rel-b",
            "type": "CHILD_OF",
            "source": "r2",
            "targets": ["r"],
            "evidence_ids": ["c1"],
        },
    ]
    assert not validate_bundle(e, StructureProposalBundle.model_validate(data)).admissible
    for c in data["relations"]:
        c["type"] = "REFERS_TO"
    assert validate_bundle(e, StructureProposalBundle.model_validate(data)).admissible


def test_rag_cannot_embed_numeric_cell_payload_field():
    with pytest.raises(ValidationError):
        RAGProjection(
            projection_id="x", canonical_sha256="a" * 64, units=[], numeric_cells=[{"value": 12}]
        )


def test_zero_policy_needs_provenance():
    from doc_understanding.agents.contracts.models import NumericPolicy

    with pytest.raises(ValidationError):
        NumericPolicy(blank="zero_explicit")


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("source_label", "SOURCE_LABEL_MATCH"),
        ("duplicate_binding", "DUPLICATE_BINDING"),
        ("wrong_axis", "PATH_NODE_TYPE"),
        ("path_edge", "PATH_EDGE"),
        ("dangling_operand", "OPERAND_REFERENCE"),
        ("scope_overlap", "SCOPE_OVERLAP"),
    ],
)
def test_additional_contract_failures(mutation, code):
    e, p = fixture()
    d = p.model_dump(mode="json")
    if mutation == "source_label":
        d["nodes"][0]["label_proposed"] = "Invented"
    if mutation == "duplicate_binding":
        d["bindings"].append({**d["bindings"][0], "binding_id": "other"})
    if mutation == "wrong_axis":
        d["bindings"][0]["row_path"] = ["h"]
    if mutation == "path_edge":
        d["bindings"][0]["row_path"] = ["r", "r2"]
    if mutation == "dangling_operand":
        d["operations"] = [
            dict(operation_id="op", operator="sum", operands=[dict(ref="missing", role="input")])
        ]
    if mutation == "scope_overlap":
        d["relations"] = [
            dict(
                claim_id="rel",
                type="REFERS_TO",
                source="r",
                targets=["r2"],
                evidence_ids=["c1"],
                scope=dict(included=["r"], excluded=["r"]),
            )
        ]
    result = validate_bundle(e, StructureProposalBundle.model_validate(d))
    assert code in [x.code for x in result.checks if x.outcome == "fail"]


def test_projection_stale_and_invented_prose():
    from doc_understanding.agents.contracts.models import CanonicalSnapshot
    from doc_understanding.agents.contracts.validation import validate_projection

    e, p = fixture()
    canonical = CanonicalSnapshot(evidence=e, proposal=p, validation=validate_bundle(e, p))
    text = dict(text="Alpha; Beta", evidence_ids=["c1"], status="source_transcription")
    d = dict(
        projection_id="rag",
        canonical_sha256=canonical.digest(),
        units=[dict(unit_id="u", level="table", summary=text, schema_narration=text)],
    )
    assert validate_projection(canonical, RAGProjection.model_validate(d))
    d["canonical_sha256"] = "f" * 64
    assert not validate_projection(canonical, RAGProjection.model_validate(d))
    d["canonical_sha256"] = canonical.digest()
    d["units"][0]["summary"] = {**text, "text": "Invented description"}
    assert not validate_projection(canonical, RAGProjection.model_validate(d))


def test_alternative_parents_are_candidates_not_errors():
    e, p = fixture()
    d = p.model_dump(mode="json")
    d["nodes"].append(dict(node_id="r3", kind="row", anchors=[dict(evidence_id="c1")]))
    d["relations"] = [
        dict(
            claim_id=f"c{i}",
            type="CHILD_OF",
            source="r",
            targets=[parent],
            evidence_ids=["c1"],
            alternative_set_id="alternatives",
        )
        for i, parent in enumerate(["r2", "r3"])
    ]
    assert validate_bundle(e, StructureProposalBundle.model_validate(d)).admissible


def test_all_generated_design_contracts_roundtrip():
    from pathlib import Path
    from doc_understanding.agents.contracts.models import CanonicalSnapshot
    from doc_understanding.agents.contracts.validation import validate_projection

    paths = list(Path("reports/architecture-v1-contract-draft/cases").glob("*/canonical.json"))
    assert len(paths) == 40
    for path in paths:
        canonical = CanonicalSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
        assert validate_bundle(canonical.evidence, canonical.proposal).admissible
        projection = RAGProjection.model_validate_json(
            path.with_name("rag.json").read_text(encoding="utf-8")
        )
        assert validate_projection(canonical, projection)


def test_paired_inputs_identical_except_candidate_channel():
    from pathlib import Path
    from doc_understanding.agents.contracts.models import InterpretationComparison
    from doc_understanding.agents.contracts.validation import validate_comparison

    folders = list(Path("reports/architecture-v1-contract-draft/cases").glob("*/comparison.json"))
    assert len(folders) == 26
    for path in folders:
        e = EvidenceBundle.model_validate_json(
            path.with_name("evidence.json").read_text(encoding="utf-8")
        )
        a = EvidenceBundle.model_validate_json(
            path.with_name("assisted-evidence.json").read_text(encoding="utf-8")
        )
        plan = InterpretationComparison.model_validate_json(path.read_text(encoding="utf-8"))
        assert validate_comparison(e, a, plan)
        changed = a.model_copy(update={"items": a.items[:-1]})
        assert not validate_comparison(e, changed, plan)
