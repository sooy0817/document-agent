# ADR-069 — Feedback to guarded local repair

Date: 2026-11-04

## Decision

A newly saved error/review revision is independently queued for one GPT-4.1 mini proposal. The review UI and model review do not wait on parser repair. Historical requests are not replayed automatically. No Claude dependency is required in automatic mode.

The worker freezes the request, PDF hash, parser code, tests, and regression manifest. It re-extracts current code first so a stale saved JSON is not mistaken for a current parser defect. The model receives one page of source cells and relevant code; repeated document ID prefixes are omitted only in the model input. Full identifiers and source artifacts remain immutable. The model cannot change acceptance criteria, tests, budgets, or validators.

Candidate code executes in a read-only, network-disabled Docker container with no real credentials. The model cannot use tools. New imports/host execution/I/O capabilities and comment-only edits are rejected conservatively. Exact replacement context is mandatory. No fuzzy application is used.

## Promotion boundary

Automatic promotion currently supports source-grounded nested-table references only. Unknown, unsupported role/path/context assertions are failed checks, not ignored. Every named source ID and quote must survive. A linked child must be unique, fully covered, totally ordered, and not linked to another parent. Existing physical cells, source spans, semantic paths, and already-linked parent records must remain identical across all datasets. At least two distinct regression PDFs must pass, fixed tests must pass, the target must improve from failing to passing, and the live code must still match the frozen version. Only then are exact candidate files applied locally. Previous source code and JSON remain preserved. No push/deployment or semantic-fact publication occurs.

A freeform note without executable source references can generate a proposal and before/after outputs but cannot authorize promotion. This is not universal autonomous parser repair or learned model training. Physical/semantic algorithm changes that modify existing cells/paths remain proposals under the current preservation gate. Holdout checks are used when configured; the current two-document registry is Hana and Gangdong regression, not independent generalization evidence.

## Dashboard

The top strip reports the last actual application. The selected feedback shows a single status: applied, proposal only, failed, or source reference needed. Applied records open their candidate result in the same page; the old collected JSON is preserved and can be reopened. The before/after buttons load separate immutable jobs. Source-condition comparison is visible; regression details, code reasons, costs and event history are collapsed.

## Verification

- 43 offline Python tests: repair queue, exact application, source acceptance, unsupported/uncertain claims, no-reference blocks, existing row-strip regressions, no-improvement block, stale-code block, and monitor API.
- 19 existing JavaScript monitor/metrics checks passed.
- Real KB page 3 attempt-9: GPT-4.1 mini, 1 call, 26,502 input / 718 output tokens, estimated USD 0.0117496. A proposal was generated but its replacement context did not match the frozen code. Rejected before candidate execution or application. No parser improvement is claimed. The proposed changes also contained only comments, which is now explicitly rejected by an offline check.
- Attempts 7 and 8 stopped before a model call because of duplicated input size; cost zero. Both remain in history. Input compaction was then applied without changing source IR.
- Previously applied KB page 2 result (ADR-068) remains separate from this failed attempt. Existing 3/7 to 7/7 source checks and Hana/Gangdong preservation are historical evidence, not new scores.

## Operations

Server: python -m doc_understanding.web. Automatic worker enabled for new feedback, two proposal calls/day, no automatic retry loop. A stopped server cannot process the queue; restart restores interrupted state without re-billing uncertain model outcomes. API failures and partial comparison artifacts remain on disk. Generated proposals are not Gold, and source-reference drafts are not human-adjudicated benchmark facts.

Browser verification: actual local Chrome confirmed applied KB page 2 opens candidate job 740c6318e6cb788a75befa9026ec995b at page 2, before toggle loads baseline, and KB page 3 shows a readable failed/not-applied reason. Screenshots: work/automatic-repair-20261104/dashboard.png and dashboard-failure.png.
