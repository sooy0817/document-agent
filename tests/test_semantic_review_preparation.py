from pathlib import Path
import json
from collections import Counter
from doc_understanding.agents.contracts.models import CanonicalSnapshot
from doc_understanding.review.semantic_description.diagnostics import diagnose

ROOT = Path("data/semantic-understanding/cases")


def test_diagnostic_totals_and_no_truth_promotion():
    rows = []
    for p in ROOT.glob("*/canonical.json"):
        c = CanonicalSnapshot.model_validate_json(p.read_text(encoding="utf8"))
        rows.extend(diagnose(c.evidence, c.proposal)["rows"])
    assert Counter(x["status"] for x in rows) == {"match": 21, "mismatch": 8, "unresolved": 59}
    assert Counter(x["primary_cause"] for x in rows if x["status"] == "unresolved") == {
        "some_cells_blank": 31,
        "all_cells_blank": 16,
        "triangle_sign_policy_missing": 7,
        "blank_and_triangle": 5,
    }
    assert all(
        not x["source_numeric_error_confirmed"] and not x["semantic_structure_error_confirmed"]
        for x in rows
    )


def test_pack_coverage_and_immutable_baselines():
    import hashlib

    pack = json.loads(
        Path("reports/semantic-human-review/review-pack.json").read_text(encoding="utf8")
    )
    assert len(pack["items"]) == 14
    assert len({(x["case_id"], x["description_id"]) for x in pack["items"]}) == 14
    assert Counter(
        {
            level: sum(v.get(level, 0) for v in pack["inventory"].values())
            for level in ["table", "metric", "analysis"]
        }
    ) == {"table": 4, "metric": 116, "analysis": 4}
    for key, hashes in pack["baseline_hashes"].items():
        for name, digest in hashes.items():
            assert hashlib.sha256((ROOT / key / name).read_bytes()).hexdigest() == digest
    for item in pack["items"]:
        c = CanonicalSnapshot.model_validate_json(
            (ROOT / item["case_id"] / "canonical.json").read_text(encoding="utf8")
        )
        assert set(item["evidence_ids"]) <= {x.evidence_id for x in c.evidence.items}
        assert item["questions"] and item["status"] == "pending"
    assert not pack["independent_gold"]


def test_saved_review_contains_question_not_independent_gold(tmp_path):
    from doc_understanding.review.semantic_description.store import DescriptionStore, DescriptionReview

    s = DescriptionStore(ROOT, tmp_path, review_pack=Path('reports/semantic-human-review/review-pack.json'),
                         guidance_path=Path('reports/semantic-priority-review/supplement.json'))
    c = s.case("G4")
    event = s.review_description(
        "G4",
        DescriptionReview(
            reviewer="Synthetic reviewer",
            expected_revision=0,
            target_id="p4r1-meaning",
            verdict="defer",
            note="Need denominator",
            proposal=c["current_proposal"],
            descriptions=c["descriptions"],
        ),
    )
    assert event["review_context"]["question"]["review_id"] == "R09"
    assert not event["review_context"]["independent_gold_confirmed"]
    assert not s.candidates()


def test_column_scope_excludes_percent_and_change_without_zero_fill():
    from doc_understanding.review.semantic_description.numeric import numeric_checks

    c = CanonicalSnapshot.model_validate_json(
        (ROOT / "G2/canonical.json").read_text(encoding="utf8")
    )
    before = c.digest()
    p = c.proposal.model_copy(deep=True)
    rel = next(r for r in p.relations if r.operation_id == "p2sum7")
    scope = rel.scope.model_copy(
        update={"included": ("p2h1-2", "p2h1-4"), "excluded": ("p2h1-3", "p2h1-5", "p2h0-6")}
    )
    p = p.model_copy(
        update={
            "relations": tuple(
                r.model_copy(update={"scope": scope}) if r.operation_id == "p2sum7" else r
                for r in p.relations
            )
        }
    )
    results = [x for x in numeric_checks(c.evidence, p) if x["operation_id"] == "p2sum7"]
    assert len(results) == 2
    assert all(x["status"] == "match" for x in results)
    assert {x["reported"] for x in results} == {"155422", "169382"}
    assert c.digest() == before
