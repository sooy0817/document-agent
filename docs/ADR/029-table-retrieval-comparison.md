# ADR-029 — Compare table descriptions, row paths and hybrid retrieval

2026-10-22. User-authorized experiment before choosing the pgvector design.

Keep CanonicalDocument/StructuredTableIR contracts, authoritative NH PostgreSQL,
existing keyword SQL, LangGraph and Langfuse. No financial facts are approved.
Use a separate local pgvector PostgreSQL service for disposable search projections;
do not replace/restart the current NH or Langfuse database. The projection refers
to immutable run/document/table/row/column/cell IDs and canonical snapshot hash.
Rehydrate selected values from the original stored CanonicalDocument and check
them with the existing SQL cell join before returning evidence.

Rule-based source excerpts, titles, axes, row paths and column paths form search
texts. Store their source references and candidate status. Never synthesize a
financial interpretation or embed evaluation questions/answers in the corpus.
LLM description candidates, if supplied, are a separate inactive projection kind
with provenance; they do not replace source-grounded descriptions in this run.

Compare A existing keyword retrieval, B table-description cosine retrieval,
C the same table candidates followed by row-path search and column-path lookup,
D keyword/vector reciprocal-rank fusion followed by the same tree lookup.
Explicit question institution/year/measure constraints are common constraints;
no reference answer, expected cell ID or expected table is given to retrieval.
Use exact cosine scan initially (avoid ANN recall as an extra variable).

Freeze a source-reviewed development question/reference set before retrieval.
Include straightforward, paraphrased, hierarchical, ratio/change, ambiguous and
unsupported-period cases. Separate source-visual-reviewed references from
IR-only diagnostics and independently human-approved gold (none supplied).
Report table Recall@5/Top1 on answerable cases, cell ID + raw-value exact match,
source/path/raw-value evidence accuracy, abstentions, timing boundaries, API cost
and invalid structure cases. Do not count a matching IR as semantic approval.

Pilot finding: choosing only the top-one table prevents row paths from correcting
a misranked description. C/D therefore examine rows across the SAME top-five
tables before the final table choice. Record original scores and final ranks.
The first diagnostic run allowed high-margin row-vector hypotheses to select
numbers. It produced real errors (unknown compound indicator, translation and
income question drift). Keep those records. Default safe mode does not promote
or answer an unanchored row hypothesis: only an explicit source-label/path match
can select a cell. Unresolved/competing source paths also block lookup. No source
status is promoted to human-validated by this gate.

The primary common institution filter often leaves only three tables, making
Recall@5 weak evidence. Add a global-ranking-only diagnostic before SQL filtering
(no numeric answers), plus keyword + exact Tree ablation to isolate the benefit
of Tree lookup from the benefit of adding vector table retrieval. This ablation
uses no embedding/vector query and does not alter baseline A.

All methods share the same corpus/snapshot and query set. Export per-question
ranked results and failures, source/JSON hashes, query plans, SQL timing and traces.
Reuse the existing LangGraph service through injected retrieval/evidence hooks;
do not modify the default original RAG path. Tests use fake vectors/providers and
the existing external-network guard; live embeddings and optional GPT answers
are separate explicit commands with budgets and a content-addressed cache.

Execution 2026-10-22: 26 documents, 87 tables, 1,622 projections and 47 frozen
development questions. Source-filtered table Top1 A/B/C/D: 42/42, 20/42, 38/42,
42/42. Cell exact match: 27/37, 14/37, 32/37, 32/37. Keyword + exact Tree also
achieved 32/37 without vector queries, so prefer that simpler demo path and keep
D optional. This corpus does not establish a hybrid advantage over keyword + Tree.
No independent human gold or economic/news holdout accuracy is claimed.
Keep unsafe diagnostic failures and final safe-mode abstentions distinct.
See reports/TABLE_RETRIEVAL_COMPARISON.md for actual outputs, timing/cost scopes,
source audits, known Windows test failure and traces in the user's pdf_agent project.
