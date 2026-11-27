# ADR-039 — Flat repeated panels and no-body diagnostics

2026-10-28. Implemented pilot; source evidence is never fact approval.

## Scope
Keep PhysicalTableIR 2.1 and StructuredTableIR 2.0 field contracts. Accept old
analyzer version 0.1.0; new executions identify semantic-geometry/0.2.0.
Preserve all original artifacts, values and IDs. No headerless-row-group repair,
header inheritance, LLM calls or DB migration in this change.

## Candidate conditions
Only complete flat grids (no merged cells) with at least two complete body rows
per panel qualify. The first-row sequence must repeat with a unique block width
of at least two columns, with distinct nonblank labels within each block.
Each block's first column must contain unique text-bearing labels; label sets
between blocks must be disjoint. Numeric-only stubs, blank labels with populated
values, missing cells and ambiguous repetitions abstain. At least two body rows
per block must have all value columns populated. Completely blank trailing rows
remain source blanks. No filenames, page numbers, source values or vocabulary
lists participate. These conditions intentionally sacrifice recall.

Repeated headers alone are insufficient. A common superheader/any merge excludes
this narrow rule. Repeated metric columns sharing the same row labels do not split.
This is not proof of semantic panel identity; one-row-header ambiguity stays
unresolved and independent human review stays pending. New panels use original
column ranges and original cell IDs. The existing Tree/path functions run within
each panel. No cropped/reconstructed Physical table or synthetic source IDs.

## Diagnostics
NO_BODY_MAPPING warns when source cells have no body Mapping. HEADER_CONSUMES_ALL_ROWS
warns when a proposed header consumes all rows. Neither invents body rows; genuine
header-only tables may trigger diagnostics. REPEATED_STUB_AS_VALUE identifies
repeated-panel label candidates treated as values, independently of the current
value-role denominator. PANEL_GEOMETRY_CONFLICT checks Mapping cell containment.
These diagnostics do not establish complete semantic recall for arbitrary tables.

## Validation boundary
Development: Hana F01 and P01 diagnostics; synthetic label/value permutations,
three-panel text/zero example and shared-row negatives. Regression: HSBC, NH,
prior merged/multilevel examples. Freeze production code/tests before new Korean
PDF acquisition/review; no tuning on those PDFs. Count unchanged, fixed, newly
wrong and abstained cases independently. New PDFs where the rule never activates
prove non-regression only, not positive generalization.

## Executed pilot
97 selected tests passed. Frozen 207 source/test/settings hashes before acquiring
new domestic Korean PDFs; no changes after source review. Development F01 known
value/row/column matches 3/5 -> 5/5; P01 retains 0/8 known row mappings and unknown
column meanings, now diagnosed. Fresh Hana H01 improves 3/6 -> 6/6 but shares the
development publisher/layout family. Woori W01 stays 3/6: a central double ruling
becomes an extra physical column and four empty cells, excluding the flat rule.
KB K01 stays 0/10: merged superheaders and native overprinted bold text are outside
this change. No holdout-driven tuning. Physical cells, trees, mappings and nested
links remain unchanged for re-extracted HSBC/NH except two new HSBC diagnostics.
All source-matching repeated-panel mappings remain unresolved; no fact approval.
See reports/semantic-panels-20261028/comparison.html and immutable evaluation JSON.
