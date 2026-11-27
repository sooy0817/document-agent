from doc_understanding.review.semantic_description.rounding import classify_display_check as check
from doc_understanding.review.semantic_description.rounding import review_outcome


def test_semantic_and_numeric_reviews_are_independent():
    assert review_outcome("accepted", "rounding_compatible") == "semantic_passed_numeric_approximate"
    assert review_outcome("pending", "rounding_compatible") == "review_required"
    assert review_outcome("rejected", "exact_match") == "semantic_failed"
    assert review_outcome("accepted", "mismatch") == "semantic_passed_numeric_investigation"
    assert review_outcome("accepted", "exact_match") == "validation_passed"


def test_weighted_linear_operation_uses_display_precision():
    r = check("mismatch", ["1.0", "2.00", "4.01"], [2, -1, 1], ".02")
    assert r["bound"] == "0.110"


def test_rounding_envelope_and_no_approval():
    r = check("mismatch", ["26,542", "20,848", "5,695"], [1, -1, 1], "-1")
    assert r["category"] == "rounding_compatible" and r["bound"] == "1.5"
    assert r["residual_interval"] == ["-2.5", "0.5"]
    assert not r["rounding_policy_confirmed"] and not r["automatically_approved"]
    assert r["cause"] == "unresolved"


def test_percent_precision_not_money_tolerance():
    r = check("mismatch", ["0.20", "2.82", "2.19", "5.20"], [1, 1, 1, 1], ".01")
    assert r["bound"] == "0.020"
    assert check("mismatch", ["1.00", "1.00", "2.10"], [1, 1, 1], ".10")["category"] == "mismatch"


def test_exact_unavailable_and_boundary():
    assert check("match")["category"] == "exact_match"
    assert not check("match")["automatically_approved"]
    assert check("unresolved")["category"] == "calculation_unavailable"
    assert check("mismatch", ["", "1"], [1, 1], 1)["category"] == "calculation_unavailable"
    assert check("mismatch", ["1", "1"], [1, 1], 1)["boundary_only"]


def test_nonfinite_or_bad_shape_is_unavailable():
    assert check("mismatch", ["NaN", "1"], [1, 1], 1)["category"] == "calculation_unavailable"
    assert check("mismatch", ["1"], [], 1)["category"] == "calculation_unavailable"
