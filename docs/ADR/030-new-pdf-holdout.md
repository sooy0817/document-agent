# ADR-030 — Frozen new-PDF generalization experiment

2026-10-23. Reuse the existing extraction, CanonicalDocument, PostgreSQL,
pgvector, A/B/C/D/A+Tree, LangGraph, Langfuse and GPT answer implementations.
No new source JSON contract, parser templates or source-specific exceptions.

Six source PDFs: development Pneuma, HiTab, BIS private-credit article;
holdout TUTA, TableFormer, BIS inflation-targeting article. IMF acquisition was
rejected with HTTP 403 before any split/content freeze and replaced. Metadata may
be used for acquisition. Holdout tables/answers must not guide implementation.
Freeze source hashes, implementation hashes and evaluation policy before
opening holdout content. Development references and holdout references remain
in evaluation-only sidecars, never in retriever inputs or indexed projections.

Run the existing pipeline without semantic repair. Report extraction failure
separately from retrieval failure and safe abstention. Visual source review by
this agent is not independent human approval. No validated facts are published.
Measure document/section/table retrieval separately from value/axis/context
agreement. Missing section recovery or unsupported metric conditions are actual
limitations, not grounds for filling references from extraction output.

An additional source-reviewed-table-only candidate ranking experiment can
isolate retrieval from extraction. It must not insert corrected IR, force the
gold table for a question, or call extracted-path agreement semantic accuracy.
After holdout opening no parser/retrieval/prompt edits in this experiment.

Completed: 6 stored/requeried documents, 112 pages, 20 questions x 5 methods.
No eligible new value cell. Among 10 source-reviewed targets, first blocker:
region detection 6, physical grid 4; semantic/mapping are upstream-blocked.
504 projections remain the denominator: 303 stored and 201 empty inputs
quarantined with originals. The frozen implementation hash check still passes.
The operational quarantine changes no parser/retriever/prompt or canonical data.
See reports/new-pdf-holdout/REPORT.md. Follow-up LLM/VLM recovery is a separate
proposal in docs/DOCUMENT_UNDERSTANDING_AGENT_PROPOSAL.md, not implemented here.
