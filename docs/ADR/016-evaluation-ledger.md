# ADR 016 Local immutable evaluation ledger

Add independent evaluation-run/1.0 and evaluation-review/1.0 contracts. Existing IR and Overlay schemas are unchanged. No database or external service.

Each experiment captures its named results, evidence references, freeze/reference files and metadata into content-addressed objects. Identical registrations are idempotent. Changed inputs or results produce a different run ID; historical results are not overwritten. Recorded time is ingestion time, not an invented historical execution time. Historical code freezes are linked where available; missing historical code is not substituted with today's code.

Review queues are versioned and hash-bound. Browser decisions remain drafts until exported and imported using the local CLI. Import validates queue identity, case membership, reviewer, duplicate decisions and correction reasons, then appends an immutable review event. A changed judgment is a new event. Review does not modify gold, approve a root, or publish facts automatically. A separate annotated revision and reevaluation must follow.

Raw text, physical geometry, structure candidates, arithmetic assumptions, human validation and confirmed facts remain separate. Arithmetic match is evidence, not a semantic label. Native/scanned, development/unseen/final holdout and geometry/semantic/answer metrics remain separate. Nampyeong stays unopened.
