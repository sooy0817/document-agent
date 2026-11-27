# ADR-070: Connect selected evidence to the campaign review team

Date: 2026-11-04. Status: first vertical slice; later approval/promotion contracts remain planned.

## Context

The campaign graph currently stops after local checks. Real specialist review is
available separately, and the user selected 13 existing N03/N08 review items.
These are development evidence, not confirmed references or an unseen test set.

## Decision

Extend the existing campaign graph with an optional specialist node. The selected
pilot imports source PDFs into the existing sample store, invokes the current parser,
performs local checks, and dispatches the real table-review team through ReviewQueue.
Old queue results remain historical evidence; new results have new request IDs.

Use the existing SQLite tables and immutable campaign sidecars for this first slice.
No schema change is introduced here. Planned tasks/leases/outbox will require the
separate versioned migration described in the implementation plan; they are not
claimed to be present in this slice. A per-campaign OS file lock prevents concurrent
execution; completed sample and queue records determine resumable work. Ambiguous
running work is blocked instead of guessed complete or silently replayed.

Pin the selection manifest, source hashes and canonical hashes. Rebind selected
regions to fresh parser output using source geometry and text, not the old ordinal
ID alone. Missing/ambiguous matches block review. Distinct tables on one page are
retained. Only development samples can reach the improvement-facing specialist team.

The pilot uses an explicit bounded additional review budget requested by the user.
Historical budget and request records are preserved. It does not clear exhausted
budgets or reset failed role artifacts. A failed request requires explicit resume;
retry uses cached successes and never silently repeats uncertain billed calls.

No RepairQueue is started by the pilot and no parser code is applied. Specialist
completion pauses with `improvement_not_connected`, not final human approval.
Generated patches, automatic workspace application, reference confirmation and
promotion remain separate subsequent work. No financial facts are published.

## Validation

Test genuine graph/queue adapters with injected model results only for control-flow
and failure cases. Also execute the actual selected PDFs with the real configured
transport and record failures honestly. A DNS failure or saved historical response
does not satisfy live end-to-end completion.
