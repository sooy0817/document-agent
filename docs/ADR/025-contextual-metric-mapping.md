# ADR-025 — Contextual MetricCandidate MVP (development only)

Existing DocumentIR, PhysicalTableIR, StructuredTableIR, Proposal, Description,
MetricCandidate 0.1 and approval ledgers stay unchanged. MetricMappingBundle 0.1
wraps the existing candidate contract with row decisions, bindings and abstentions.

Definitions: total assets = all assets at an instant; total liabilities = all
liabilities at that instant, excluding equity; total equity = residual equity,
excluding liabilities. Operating revenue = whole-statement operating revenue for
a duration, not a business-segment component. Net income = after-tax final profit
or loss for a duration, not pre-tax income, per-share/per-unit income or a ratio.
These are proposed metric definitions, not inferred approvals of any source.

Substring label search is retained only as the baseline retrieval stage. The
offline contextual rule mapper combines measure type, statement family, table
headers, geometric column scope, StructuredTableIR paths, anchored proposal
relations and descriptions. No external model executes. It is not a newly tested
Agent or Hybrid model. Missing/contradictory context results in ABSTAIN.
Descriptions/hypotheses and Rule Trees are never independent truth.

Value/period/unit/institution/accounting scope bind independently. Year alone
does not establish an instant date or full duration. Uncertain cross-page scopes
remain candidates. Blank/dash remain missing, not zero. Unsigned loss rows require
sign review. Percent/change columns cannot supply absolute monetary metrics.

Store derived bundles in new staging.processing_run + artifact records, referencing
the source staging run. Do not append derived artifacts to immutable extraction
runs (that would invalidate their idempotency fingerprint). No DB schema change,
Financial Fact, final Gold approval or production publication.

Evaluation compares identical anchored rows, not all possible document metrics.
Original-source adjudications are provisional assisted development annotations;
holdout accuracy and independently reviewed accuracy remain N/A. Agent/Hybrid
evaluation adapters accept the same decision manifest, but unexecuted arms are N/A.
Human UI edits append separate local review events with before/after/evidence hash;
export is required for later server import. Existing approval histories are untouched.
