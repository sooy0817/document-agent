# ADR-049 — Period definitions linked separately from axis ancestry

Date: 2026-10-30. Status: opt-in experiment; no automatic semantic approval.

## Problem

A duration label may need an explicit start/end definition. Axis-only paths can
preserve the number while omitting that definition. Adding every explanatory
heading to row ancestry conflates field definitions and category hierarchies.

## Decision

Keep DocumentIR, PhysicalTableIR, StructuredTableIR, CanonicalDocument and saved
axis proposals unchanged. Write a separate versioned period-context sidecar with
`defines_period_basis` relations referencing existing cells. Each application is
an explicit `(row_cell_id, value_cell_id)` pair; separate arrays must not imply a
cross-product. Resolve raw text/coordinates/spans from the original registry.

An opt-in LangGraph calls a source-grounded proposer once, checks ID/evidence
integrity, then calls an independent source-only Judge once. The Judge receives
no proposals, axis paths, expected answers, previous judgement, or case names.
It inventories required relations, absence, or insufficient evidence per target.
The same frozen inventory is replayed against empty and generated relation lists.
This isolates relationship changes from Judge resampling; it does NOT constitute
an independent human evaluation. Same-model correlated errors remain possible.

Missing expected edges score completeness=1; complete edges=2. Extraneous scope
edges score scope=0. Unknown input scope yields null. Invalid IDs, missing evidence,
duplicate/inconsistent inventories or upstream errors block scoring. No score or
geometry check changes semantic_verified, training approval, or financial facts.

Period wording is interpreted by the model from source. No institution, filename,
page number, expected amount or exact phrase is used in runtime routing. Development
manifests select examples only. A clearly labelled synthetic scope mutation tests
whether the same inventory rejects an unrelated application; it is not a newly
observed parser failure. Historical axis errors remain unchanged and reported.

## Validation and scope

Offline tests inject a fake key and mocked transport under the existing network
block. Explicit CLI `--allow-external` is required for live model calls. Calls,
usage, estimates, model inputs and failures are saved in a new immutable run.
Existing local Langfuse spans/scores are reused with bounded evidence summaries.
No production ingestion, DB, RAG answer rendering or old Judge behavior changes.
This first experiment covers selected iM and Hana mappings, not arbitrary notes,
cross-page periods or general PDF accuracy. Results are recorded in the run report.
