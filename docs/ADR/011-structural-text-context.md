# ADR-011 — Structural text context and extraction envelope

Status: local offline implementation, no live evaluation yet.

Keep PyMuPDF and all existing DocumentIR/PhysicalTableIR/StructuredTableIR/SemanticOverlay contracts.
Add an opt-in text-context/1.0 request containing request-local aliases, adjacency, explicit current
parents (Hybrid only), occupancy, physical boundaries, native text alignment and context completeness.
Numeric value text and images are excluded. All geometry/evidence states are copied, not repaired.
Missing context is explicit. Region selection is not semantic truth. No account-name/year/page rules.

Response combines existing ADD/REPLACE/ABSTAIN with root claims and short-ID aggregation scopes.
Request digest and registry hash both bind a response. The source binder supplies original evidence.
Root claims never silently delete an existing parent; conflict is retained for review. Non-parent
relations do not enter the classification tree. A target needs explicit hierarchy disposition even
if it also participates in aggregation. All claims remain review_required/rejected, never facts.

CanonicalExtractionEnvelope/0.1 is a separate draft storage envelope, not a replacement of the old
CanonicalDocument draft or source IR. Preserve raw JSON bytes in immutable storage and SHA-256 plus
JSONB projection; JSONB alone does not preserve original byte representation. Failed parsing runs
and partial/unresolved artifacts are first-class. Financial Fact publication is disabled.

No new API calls, model downloads, PostgreSQL migrations or ingestion in this change. Namwon is
development-only; future live trial requires payload/budget approval. Nampyeong remains unopened.
