# ADR-052 — bounded original context assembly and one rejudgment

Date: 2026-11-03. Opt-in experiment; no production default change.

Coverage absence is not semantic absence. Use ADR-051 findings to retrieve existing
usable Physical cells from the same immutable extraction. Match Native audit owners
by original span subsets and containment; prefer unique exact span identity. Never
invent a cell, relax geometry tolerance, rewrite source text or match institution names.

Copy cells into an ephemeral evidence packet with original page identity and source
IDs. Coordinates stay page-local. Continued-page candidates are context only, not
confirmed logical-table links. Keep the initial region and every source IR unchanged.
If a counterpart is absent, ambiguous or exceeds 180 cells/100KB, keep the gap and
abstention. Images are bounded source crops labelled by original page, never redraws.

LangGraph: collect_missing_source_context → conditional rejudge_once → end.
One retry means at most two fresh API calls: generator plus independent source-only
Judge. Neither gets the previous response or evaluation reference. Use the same
DecisionAdapter prompts, model, schema and validation. Re-audit expanded source and
apply the absence guard again. No iterative repair or automatic approval.

Evaluation compares saved original results, fresh original-input controls and fresh
expanded-input results. Response variation is not removed by temperature=0; this
small development experiment cannot identify a universal effect or full row/column
accuracy. Coverage, model agreement and human source correctness remain separate.
Previous source-review references are drafts, not independently approved Gold.

No DocumentIR/PhysicalTableIR/StructuredTableIR/CanonicalDocument schema, PostgreSQL,
RAG, parser rules, row/column mappings or historical results are modified.
