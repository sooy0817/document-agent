# ADR-040: Existing nested references in RAG evidence

2026-10-29. Implementation scope: source-based nested narrative retrieval and answer context.

CanonicalDocument, DocumentIR, PhysicalTableIR and StructuredTableIR remain unchanged.
Use the existing span-geometry/2 sidecar as a reproducible projection of each snapshot.
Only unique linked parents with complete span coverage and total reading order supply
answer context. Unknown ordering/containment is not silently promoted. No semantic
ambiguity is cleared. Hana p4 fee values already have eligible inferred paths.

`cell_text` search entries retain content ID, parent cell ID, source spans, exact
character offsets, and child table IDs. Split at text boundaries, at most 600 characters,
without overlap or loss. Child table spans never become narrative chunks. Narrative
hits expand to eligible child cells; child hits carry the same parent prose. The ordinary
body lane excludes these parent-owned fragments to avoid losing their scope.

Evidence packs include bounded verbatim parent context (maximum 6000 characters;
oversized packages are not silently truncated). Applicability and note scope remain
unvalidated. GPT-4.1 selects original evidence IDs/values and the deterministic renderer
shows the value plus parent context citations. No arithmetic or free-form financial
recommendation is introduced. Explicit latest/current-effectivity questions abstain
without an API call because the snapshots do not verify current applicability.

The index version is document-rag/0.3-nested-context; new experiment runs use new IDs.
Do not rebuild historical indexes during this experiment. JSONB source probes compare
the parent cell and each narrative span/text against the stored original document.
Only explicitly executed E2E commands may call the model; pytest keeps mock credentials
and the existing network block. Evaluation records separate extraction, mapping,
retrieval, SQL equality, model selection, abstention and actual usage.

Not implemented here: vector narrative indexing, general long-cell support outside
linked nested parents, inferred cross-page table continuation, semantic LLM patches,
or independent generalization evaluation. Existing seen PDFs are development/regression.

Executed local experiment: `scripts/run_hana_nested_e2e.py` stages `prepare`,
`ingest`, `preview`, and explicit `ask --allow-external`. `--question-id H1`
selects a demonstration question; `--output-tag <new-tag>` keeps repeat answers
separate. Initial extraction/storage outputs are intentionally non-overwriting.
`scripts/verify_hana_nested_e2e.py` audits existing source references, SQL and
Langfuse readback; its gold never enters the execution pipeline.

2026-10-29 result: two full PDFs processed and round-tripped; Hana p4 has 11/11
geometry/merge and 4/4 eligible value/path reference matches. Raw text including
whitespace matches only 5/11 PDF region references (pre-existing trailing-space
normalization); do not label it exact full-table preservation. Gangdong's 160
page2 values/paths match the existing visual reference. Six demonstration questions
yield four cited extraction answers and two abstentions, not an independent
accuracy estimate. GPT-4.1 selects evidence; the final wording remains deterministic.
See `reports/hana-nested-e2e-20261029/comparison.html` and its verification JSON.
