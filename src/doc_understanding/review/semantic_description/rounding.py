"""Conditional display-rounding assessment, never semantic or source-error approval."""

from decimal import Decimal


def review_outcome(semantic_status, numeric_category):
    """Combine independent axes for display, never promote a semantic review."""
    if semantic_status in ("rejected", "failed"):
        return "semantic_failed"
    if semantic_status not in ("accepted", "corrected"):
        return "review_required"
    return {
        "exact_match": "validation_passed",
        "rounding_compatible": "semantic_passed_numeric_approximate",
        "mismatch": "semantic_passed_numeric_investigation",
        "calculation_unavailable": "semantic_passed_numeric_unavailable",
    }.get(numeric_category, "semantic_passed_numeric_unavailable")


def classify_display_check(status, raw_values=(), coefficients=(), difference=None):
    result = dict(
        policy_version="display-rounding/0.1",
        rounding_policy_confirmed=False,
        cause="unresolved",
        automatically_approved=False,
        assumption="independent rounding to nearest displayed increment; tie rule unknown",
        unit="same displayed unit as inputs/result",
        bound=None,
    )
    if status == "unresolved":
        return dict(result, category="calculation_unavailable")
    if status == "match":
        return dict(result, category="exact_match", cause="displayed_values_equal")
    if (
        status != "mismatch"
        or difference is None
        or len(raw_values) != len(coefficients)
        or not raw_values
    ):
        return dict(result, category="calculation_unavailable")
    try:
        values = [Decimal(v.replace(",", "").strip()) for v in raw_values]
        if not all(v.is_finite() for v in values):
            raise ValueError("nonfinite")
        quanta = [Decimal(1).scaleb(v.as_tuple().exponent) for v in values]
        bound = sum(
            (q * abs(Decimal(str(a))) / 2 for q, a in zip(quanta, coefficients)), Decimal(0)
        )
        delta = Decimal(str(difference))
        if not delta.is_finite():
            raise ValueError("nonfinite")
    except (ValueError, ArithmeticError):
        return dict(result, category="calculation_unavailable")
    return dict(
        result,
        category="rounding_compatible" if abs(delta) <= bound else "mismatch",
        bound=str(bound),
        display_increments=[str(q) for q in quanta],
        difference=str(delta),
        residual_interval=[str(delta - bound), str(delta + bound)],
        boundary_only=abs(delta) == bound,
        interpretation="compatible with assumed envelope, not proof of rounding"
        if abs(delta) <= bound
        else "outside assumed nearest-rounding envelope; cause still unresolved",
    )
