# ADR-038 — Nested representation audit and independent reading-order status

Date: 2026-10-28. Status: implemented; limited pilot completed.

## Scope

ADR-037 exact identity remains unchanged. PhysicalTableIR 2.1,
StructuredTableIR, CanonicalDocument and stored original JSON remain unchanged.
Only the recomputable `nested-cell-content/1.0` sidecar receives additive fields
and `linkage_algorithm=span-geometry/2`. Old outputs remain readable but cannot be
validated as a new execution; generate a separate sidecar, never overwrite.
No semantic Tree edits, LLM, RAG, DB, or document-specific conditions.

## Partial representation

A native table is excluded from the active child sequence only when its source
span set is a nonempty strict subset of one physical grid, and its cells form a
complete full-height column strip of that grid. All cells, including blanks,
must match physical cell boxes within the existing 1.5pt tolerance and have
identical nonblank span IDs. Unknown spans, duplicate assignments, unsupported
span centers, crossing merged cells and multiple matching grids block acceptance.
Both regions must have the same unique containing native cell and all physical
spans must belong to it. No label/value similarity is used.

Accepted and rejected pairs are retained in `partial_representations` with cell
pairs, shared spans, parent and reasons. Raw Native and Physical representations
remain intact. This is an active-view alias, not an assertion of semantic identity.
Inset grids or partial-height strips remain unresolved. Full-height abutting
independent tables remain a limitation: this rule describes redundant cell
representations within an existing reconstructed grid, not semantic table identity.

## Independent statuses

`containment_status` reports physical-child identity and unique parent support.
`coverage_status` reports missing, duplicate, conflicting or outside parent spans.
`reading_order_status` is total, partial or unresolved. `semantic_status` remains
not_validated independently. Legacy `status` is conservative: any reason,
including partial order, remains unresolved for consumers unaware of new fields.

`reading_groups` preserves all item IDs. Groups are vertically sequenced:
- single: one source item;
- line: nearby, nonoverlapping horizontal text fragments with matching top/bottom
  coordinates, single-line original span boxes and original LTR direction;
- parallel: horizontally disjoint tables overlapping vertically; no relative
  reading order is asserted within the group;
- ambiguous: other overlapping arrangements, kept for review.

Inline text gap is limited to twice the smaller line height, with unchanged
1.5pt baseline tolerance. These are explicit geometric heuristics, not verified
sentence/semantic relationships. The flat items list within parallel/ambiguous
groups is deterministic serialization only. Consumers needing a total order
must require `reading_order_status=total` AND valid coverage/containment.
The legacy `order_items` helper remains unchanged for frozen experiments.

## Evaluation

Development: previously inspected Seoul property comparison, Hana FX/deposit.
Regression: HSBC, Gangdong NH, earlier Hana and prior negative cases.
New Korean PDF selection/Gold must happen after code freeze and before parsing.
Report preservation, duplicate removal, order constraints, unresolved cases and
unchanged semantic errors separately. No tuning after holdout inspection.

Results: P01 duplicate span occurrences 34 -> 0; inline text grouping resolved;
F05 four parallel tables retain partial order with separate linked containment.
HSBC/NH fresh extraction preserves all stable IR fields; six prior documents'
item sequences unchanged. New Korean PDF N02: 2 nested tables /18 physical cells
and both parent/order links match the source. N01: no nested links on a source
with no nested tables. Both new documents share publisher families with earlier
data; neither exercises a new accepted partial-duplicate pattern. Thus the new
data demonstrates non-regression, not duplicate-recovery generalization.
61 focused tests pass. Prior artifacts 426 unchanged. No semantic repair started.
See `reports/nested-order-20261028/comparison.html` and its full evidence links.
