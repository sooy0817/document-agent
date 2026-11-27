# ADR-021 — Conditional display-rounding diagnostic

Additive diagnostic only; frozen IRs, NumericPolicy and saved checks are unchanged.

For a linear sum y = sum(a_i*x_i), raw displayed increments q_i and q_y give a hypothetical nearest-rounding residual envelope E = (sum(abs(a_i)*q_i)+q_y)/2. All operands and result must use compatible displayed units, periods and scope. Negative coefficients use absolute value in the bound. Boundary-only compatibility remains unresolved because tie rules are unknown. Trailing decimal places are preserved. A displayed increment is inferred from text, not a confirmed accounting policy.

Four categories: exact_match, rounding_compatible, mismatch (outside the assumed envelope), calculation_unavailable. None approves semantic truth. Even mismatch does not prove an incorrect source number: scope, mapping, truncation or another policy may differ. Rounding-compatible is not verified rounding. Blank, unparsed symbols, missing units/scope, unsupported nonlinear operations need review. No zero filling, source correction or parent inference is performed.

R06's semantic acceptance and unresolved arithmetic cause are preserved. The 8 historical differences are evaluated against unchanged historical inputs. Current review revisions and historical baseline checks are separately identified. Human-assisted review snapshots are development evaluation candidates, not independent Gold; no new human review is generated here.

## User-authorized review update

R06 follow-up records semantic acceptance independently of numeric verification and
registers only that reviewed description as a Gold candidate. Final Gold and publication
remain pending. Prior events and immutable source checks are retained.

The additive diagnostic `numeric_validation_status` exposes
`approximate_match_within_display_rounding` for `rounding_compatible` (UI:
반올림 허용범위 내 근사 일치). This is a policy classification, not a confirmed cause.
Semantic accepted + exact => passed; accepted + approximate => semantic passed/numeric
approximate; accepted + outside envelope => numeric investigation; rejected semantics
=> semantic failed regardless of arithmetic; missing semantic evidence => review required.
No numeric result creates semantic approval. Linear coefficients determine the error
bound; nonlinear ratios require interval propagation and a denominator interval excluding
zero before a bound can be issued. Such nonlinear verification is not implemented here.
