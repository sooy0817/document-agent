# ADR-031 — Independent physical recovery experiment

2026-10-23. User-authorized follow-up; ADR-030 results stay frozen.
Old six PDFs (including former holdout) are now development data. No new
independent-generalization claim uses them. New TAPAS and TAPEX PDFs are reserved
by URL before content inspection; implementation and evaluation are frozen before
opening them. References live in evaluation sidecars, never parser inputs.

Add an opt-in physical_experiment package, not a replacement production parser.
Compare baseline, S1, S2, S3, S1+S2, S1+S3, S2+S3, S1+S2+S3.
S1 retains short vertical fragments only with repeated collinear support and
uses text-height-scaled boundary tolerance. Raw fragments remain source evidence.
S2 prepares raster evidence from the same vector-empty/fill-present predicate
that selects it. S3 proposes text-aligned regions/columns and explicitly inferred
boundaries, without pretending text gutters are observed strokes.

Reuse PhysicalTableIR 2.1, StructuredTableIR 2.0, semantic analyzer and Canonical
assembler unchanged. New snapshots have separate output directories. Preserve
source hashes, IDs, exact source strings, blanks, signs and unresolved mappings.
No LLM/VLM, API calls, SQL mutations, deployment, or automatic fact publication.

Metrics: source-annotated cell text/geometry, row/column partitions and spans,
selected source parent paths, false cuts/extra cells; baseline NH geometry/path
regression separately from semantic accuracy. Unknown reference coverage is
reported as unreviewed, never counted correct. Region/cell count alone is not
success. Holdout failure must not trigger another implementation revision.
