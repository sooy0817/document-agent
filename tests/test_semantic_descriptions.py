from pathlib import Path
import json
from doc_understanding.review.semantic_description.models import (
    SemanticDescriptionBundle,
    Description,
    validate_descriptions,
)
from doc_understanding.agents.contracts.models import CanonicalSnapshot
import pytest
from pydantic import ValidationError


def test_analysis_cannot_be_stored_as_fact():
    with pytest.raises(ValidationError):
        Description(
            description_id="x",
            level="analysis",
            subject_ids=["n"],
            text="hypothesis",
            evidence_ids=["e"],
            epistemic_status="inferred",
        )


def test_tree_prose_assertion_conflict_and_stale_hash():
    p = Path("reports/architecture-v1-contract-draft/cases/N42-0/canonical.json")
    c = CanonicalSnapshot.model_validate_json(p.read_text(encoding="utf-8"))
    bundle = SemanticDescriptionBundle(
        evidence_sha256=c.evidence.digest(),
        proposal_sha256=c.proposal.digest(),
        origin="mock",
        interpreter="test",
        descriptions=[
            dict(
                description_id="d",
                level="metric",
                subject_ids=["r2"],
                text="r2 belongs to r3",
                evidence_ids=["e10"],
                epistemic_status="inferred",
                assertions=[
                    dict(relation_type="CHILD_OF", source="r2", targets=["r3"], claim_id="member2")
                ],
            )
        ],
    )
    result = validate_descriptions(c.evidence, c.proposal, bundle)
    assert not result["structurally_consistent"]
    assert any(
        x["code"] == "TREE_DESCRIPTION_CONFLICT" and x["status"] == "conflict"
        for x in result["checks"]
    )
    assert result["accuracy"]["semantic_prose"] == "unreviewed"


@pytest.mark.parametrize("key", ["G2", "G3", "G4", "K12"])
def test_real_source_packages(key):
    from doc_understanding.agents.contracts.validation import validate_bundle

    path = Path("data/semantic-understanding/cases") / key
    c = CanonicalSnapshot.model_validate_json((path / "canonical.json").read_text(encoding="utf8"))
    d = SemanticDescriptionBundle.model_validate_json(
        (path / "descriptions.json").read_text(encoding="utf8")
    )
    assert validate_bundle(c.evidence, c.proposal).admissible
    assert validate_descriptions(c.evidence, c.proposal, d)["structurally_consistent"]
    assert d.origin == "interactive_agent_source_analysis"
    assert not d.fact_publication_allowed
    comparison = json.loads((path / "comparison.json").read_text(encoding="utf8"))
    assert "not_executed" in json.dumps(comparison)


def test_review_history_and_separate_gates(tmp_path):
    from doc_understanding.review.semantic_description.store import DescriptionStore, DescriptionReview
    from doc_understanding.review.human.store import GateInput

    s = DescriptionStore(Path("data/semantic-understanding/cases"), tmp_path)
    c = s.case("G3")
    original = c["canonical"]
    target = c["descriptions"]["descriptions"][0]["description_id"]
    r = DescriptionReview(
        reviewer="Synthetic reviewer A",
        expected_revision=0,
        target_id=target,
        verdict="correct",
        proposal=c["current_proposal"],
        descriptions=c["descriptions"],
    )
    s.review_description("G3", r)
    assert s.candidates() == []
    s.gate(
        "G3",
        GateInput(
            reviewer="Synthetic reviewer A",
            expected_revision=1,
            action="candidate",
            note="Synthetic test only",
        ),
    )
    assert len(s.candidates()) == 1
    with pytest.raises(ValueError):
        s.gate(
            "G3",
            GateInput(
                reviewer="Synthetic reviewer A",
                expected_revision=2,
                action="gold_final",
                note="Synthetic test only",
            ),
        )
    s.gate(
        "G3",
        GateInput(
            reviewer="Synthetic reviewer B",
            expected_revision=2,
            action="gold_final",
            note="Synthetic test only",
        ),
    )
    c = s.case("G3")
    c["descriptions"]["descriptions"][0]["text"] += " Synthetic amendment."
    s.review_description(
        "G3",
        r.model_copy(
            update=dict(
                expected_revision=3,
                verdict="incorrect",
                note="Synthetic correction",
                descriptions=c["descriptions"],
            )
        ),
    )
    assert s.candidates() == []
    assert s.case("G3")["canonical"] == original
    assert s.case("G3")["events"][-1]["description_changed"]


def test_bad_assertion_rejected_and_defer_not_candidate(tmp_path):
    from doc_understanding.review.semantic_description.store import DescriptionStore, DescriptionReview

    s = DescriptionStore(Path("data/semantic-understanding/cases"), tmp_path)
    c = s.case("G3")
    target = c["descriptions"]["descriptions"][0]["description_id"]
    r = DescriptionReview(
        reviewer="Synthetic test",
        expected_revision=0,
        target_id=target,
        verdict="defer",
        note="Need context",
        proposal=c["current_proposal"],
        descriptions=c["descriptions"],
    )
    s.review_description("G3", r)
    assert not s.candidates()
    c = s.case("G3")
    d = next(x for x in c["descriptions"]["descriptions"] if x["assertions"])
    d["assertions"][0]["targets"] = ["nonexistent"]
    with pytest.raises(ValueError):
        s.review_description(
            "G3", r.model_copy(update=dict(expected_revision=1, descriptions=c["descriptions"]))
        )


def test_meaning_routes_read_only(tmp_path):
    from fastapi.testclient import TestClient
    from doc_understanding.review.human.app import create_app

    with TestClient(create_app(output=tmp_path)) as client:
        assert client.get("/meaning").status_code == 200
        data = client.get("/api/meaning").json()
        assert len(data) == 4
        case = client.get("/api/meaning/G3").json()
        assert case["revision"] == 0
        assert case["description_validation"]["structurally_consistent"]
        assert client.get("/api/meaning/G3/source/3").headers["content-type"] == "image/png"
        assert not client.get("/api/meaning-candidates").json()["items"]
