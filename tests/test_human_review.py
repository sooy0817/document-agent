from pathlib import Path
import pytest
from doc_understanding.review.human.store import ReviewStore, ReviewInput, GateInput


def store(tmp_path):
    return ReviewStore(Path("reports/architecture-v1-contract-draft/cases"), tmp_path)


def review(s, verdict="correct", reviewer="Alice"):
    c = s.case("N42-0")
    return ReviewInput(
        reviewer=reviewer,
        expected_revision=0,
        target_id="member2",
        verdict=verdict,
        evidence_ids=["e5", "e10"],
        note="원본 확인",
        after=c["current_proposal"],
    )


def test_approval_gates_separate_and_revision_bound(tmp_path):
    s = store(tmp_path)
    event = s.review("N42-0", review(s))
    assert s.case("N42-0")["gates"]["gold_candidate"] == "pending"
    s.gate(
        "N42-0",
        GateInput(reviewer="Alice", expected_revision=1, action="candidate", note="검토 승인"),
    )
    assert s.case("N42-0")["gates"]["gold_final"] == "pending"
    with pytest.raises(ValueError):
        s.gate(
            "N42-0",
            GateInput(reviewer="Alice", expected_revision=2, action="gold_final", note="동일인"),
        )
    s.gate(
        "N42-0",
        GateInput(reviewer="Bob", expected_revision=2, action="gold_final", note="독립 확인"),
    )
    assert s.case("N42-0")["gates"]["publication"] == "pending"
    assert event["fact_published"] is False


def test_deferred_cannot_be_candidate(tmp_path):
    s = store(tmp_path)
    s.review("N42-0", review(s, "defer"))
    with pytest.raises(ValueError):
        s.gate(
            "N42-0",
            GateInput(reviewer="Alice", expected_revision=1, action="candidate", note="bad"),
        )


def test_stale_write_and_source_immutable(tmp_path):
    s = store(tmp_path)
    r = review(s)
    s.review("N42-0", r)
    with pytest.raises(ValueError):
        s.review("N42-0", r)
    assert s.case("N42-0")["canonical"]["proposal"] == r.after


def test_correction_keeps_before_and_invalidates_gates(tmp_path):
    s = store(tmp_path)
    r = review(s, "incorrect")
    r.after["relations"][0]["targets"] = ["r3"]
    e = s.review("N42-0", r)
    assert e["status"] == "corrected"
    assert e["before"]["relations"][0]["targets"] == ["r1"]
    assert e["after"]["relations"][0]["targets"] == ["r3"]


def test_non_target_edits_and_invalid_ids_rejected(tmp_path):
    s = store(tmp_path)
    r = review(s, "incorrect")
    r.after["nodes"][0]["node_id"] = "unknown"
    with pytest.raises(ValueError):
        s.review("N42-0", r)


def test_candidates_accumulate_without_promoting_unreviewed_relations(tmp_path):
    s = store(tmp_path)
    s.review("N42-0", review(s))
    s.gate(
        "N42-0", GateInput(reviewer="Alice", expected_revision=1, action="candidate", note="승인")
    )
    r = review(s)
    r.expected_revision = 2
    r.target_id = "member3"
    s.review("N42-0", r)
    s.gate(
        "N42-0", GateInput(reviewer="Alice", expected_revision=3, action="candidate", note="승인")
    )
    assert {x["target_id"] for x in s.candidates()} == {"member2", "member3"}
    r = review(s, "incorrect")
    r.expected_revision = 4
    r.after["relations"][0]["targets"] = ["r3"]
    s.review("N42-0", r)
    assert s.candidates() == []


def test_empty_whole_structure_cannot_be_gold(tmp_path):
    s = store(tmp_path)
    r = review(s)
    r.target_id = None
    s.review("N42-0", r)
    with pytest.raises(ValueError):
        s.gate(
            "N42-0",
            GateInput(reviewer="Alice", expected_revision=1, action="candidate", note="승인"),
        )


def test_publication_gate_does_not_publish(tmp_path):
    s = store(tmp_path)
    s.review("N42-0", review(s))
    with pytest.raises(ValueError):
        s.gate(
            "N42-0",
            GateInput(reviewer="Admin", expected_revision=1, action="publication", note="승인"),
        )
    s.gate(
        "N42-0", GateInput(reviewer="Alice", expected_revision=1, action="candidate", note="승인")
    )
    s.gate(
        "N42-0", GateInput(reviewer="Bob", expected_revision=2, action="gold_final", note="승인")
    )
    e = s.gate(
        "N42-0", GateInput(reviewer="Admin", expected_revision=3, action="publication", note="승인")
    )
    assert e["fact_published"] is False
    assert s.case("N42-0")["gates"]["publication"] == "approved"


def test_http_review_source_origin_and_export(tmp_path):
    from fastapi.testclient import TestClient
    from doc_understanding.review.human.app import create_app

    app = create_app(output=tmp_path, cases=Path('reports/architecture-v1-contract-draft/cases'),
                     source_catalog=Path('reports/architecture-v1-analysis/catalog.raw.json'))
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert len(client.get("/api/cases").json()) == 40
        page = client.get("/api/cases/N42-0/source/42")
        assert page.status_code == 200 and page.content.startswith(b"\x89PNG")
        assert client.get("/api/cases/N42-0/source/99").status_code == 404
        payload = review(store(tmp_path)).model_dump(mode="json")
        assert (
            client.post(
                "/api/cases/N42-0/review",
                json=payload,
                headers={"Origin": "https://untrusted.example"},
            ).status_code
            == 403
        )
        assert client.post("/api/cases/N42-0/review", json=payload).status_code == 200
        assert client.get("/api/gold-candidates").json()["items"] == []
        assert client.post("/api/cases/N42-0/review", json=payload).status_code == 409


def test_precheck_recomputes_bound_scopes_without_gold_promotion():
    from doc_understanding.review.human.precheck import recompute

    result = recompute(Path.cwd(), legacy_path=Path('data/tree-phase3a-triage/arithmetic.json'),
                       cases=Path('reports/architecture-v1-contract-draft/cases'))
    assert result["legacy_counts"] == {"match": 39, "mismatch": 1}
    assert result["proposal_counts"] == {"match": 4}
    assert result["gold_approval"] is False
    assert result["semantic_truth"] == "not_established"
    assert result["scope_not_inferred_from_numbers"] is True
    mismatch = next(c for c in result["legacy_checks"] if c["status"] == "mismatch")
    assert mismatch["difference"] == "-1"
    assert mismatch["rounding_hypothesis"]["unrounded_sum"] == "3656615.68"
    assert mismatch["rounding_hypothesis"]["rounded_sum"] == "3656616"
    assert mismatch["status"] == "mismatch"  # Rounding hypothesis never rewrites original check.


def test_precheck_ui_keeps_advanced_and_fact_gates(tmp_path):
    from fastapi.testclient import TestClient
    from doc_understanding.review.human.app import create_app

    with TestClient(create_app(output=tmp_path,
        cases=Path('reports/architecture-v1-contract-draft/cases'),
        precheck_path=Path('data/human-review/precheck.json'),
        source_catalog=Path('reports/architecture-v1-analysis/catalog.raw.json'))) as c:
        assert "먼저 검산한 결과" in c.get("/arithmetic").text
        assert "구조 해석 검토실" in c.get("/advanced").text
        assert c.get("/api/precheck").json()["fact_published"] is False
        assert c.get("/api/cases/N46-1/source/46?table_crop=true").status_code == 200
