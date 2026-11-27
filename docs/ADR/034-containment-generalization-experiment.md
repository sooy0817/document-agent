# ADR-034: Isolated containment generalization experiment

2026-10-26. Experimental; not enabled by build, RAG, or database writers.

The current nested-cell-content/1.0 output and all original IR contracts remain
unchanged. E0 is the existing implementation. E1 adds source-anchored layout
frames; E2 adds conservative candidate evidence; E3 combines both. All variants
consume the exact same CanonicalDocument. No physical or semantic reconstruction
policy changes, new OCR, LLM, embeddings, or DB migrations are in this experiment.

M1 reuses the existing vector line detector, component touching rule, tolerance,
and interval union. A component having exactly two horizontal and two vertical
coordinate bands and four complete observed sides is a *layout frame*, not a
data cell. It must not duplicate an existing native cell or physical table.
Tables must be strictly contained. Only the innermost unambiguous owner may be
proposed. Original spans outside child tables and original table references form
the ordered sequence. Conflicting ownership or overlapping sequence items remains
unresolved. Geometry never upgrades semantic validation. Cross-page association
and curved/dashed/open frame reconstruction are out of scope.

M2 does not equate missing PhysicalTableIR with a false table. Native-only child
candidates are checked against original PDF drawing and span evidence. A candidate
can be excluded from nested assembly only when all of the following hold: it is
one row, no matching physical child, no observed stroke crossing its area,
an original fill rectangle matches the candidate extent, and an original text
span crosses an alleged internal column boundary. Such a split-through-text
background is retained in the decision ledger. Otherwise retain the existing
candidate or abstain. No word, institution, year, page, expected row count, or
Gold is available to the runtime API. One-row real tables are not blanket-excluded.

New source-linked envelopes are emitted as containment-experiment/1.0 sidecars.
They embed an unchanged baseline result plus optional decision and layout-frame
records, with source hash, policy values and raw provenance. This is an experiment
contract, not a replacement Document JSON schema or a production migration.

Evaluation rules and source-family reservation precede result inspection.
Unacquired sources, unknown Gold, extraction failures and unsupported structures
remain in the ledger. Source content exposed by search is disclosed; no claim of
human-blind independent assessment is made. Holdout PDF inspection follows code
and policy freeze, and no algorithm tuning is allowed afterward. Source-only Gold
is never passed to runtime functions. Per-stage and end-to-end results are kept
separate; zero content mutation is not proof of extraction completeness.

Existing PDF results, Gold corrections, unresolved semantics and test behavior
are protected by file hashes. Prototype evidence determines adoption; no automatic
promotion to the default parser is allowed.
