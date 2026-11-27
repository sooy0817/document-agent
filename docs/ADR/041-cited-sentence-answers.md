# ADR-041 — Cited generated sentences without changing document IR

2026-10-29. Add opt-in `answer_mode=narrative` to the existing GPT-4.1 transport,
service, CLI and LangGraph path. Selection mode stays available unchanged.
No parser, semantic tree, CanonicalDocument, DB schema, or search algorithm changes.

The model writes Korean sentences, each with exact source quotations and source IDs.
The derived catalog deduplicates parent prose and preserves content/cell/span IDs,
page, unit/notes and original row/column paths locally. Only bounded excerpts go to
the model. Parent text remains separate from value evidence; it cannot clear an
unresolved numeric mapping. The existing SQL checks restore parent text and selected
cell values/paths before accepting the E2E result.

Validation checks IDs, exact quotes/values, numeric tokens, complete quoted condition
clauses, and coverage of bullet-shaped condition candidates when a condition question
is asked. Target terms must be in the question. Explicit Alias(Target) pairs allow
lexical scope comparison: direct mention, explicit exclusion, same-parent general
context, or uncertainty. No bank names, PDF IDs, expected values or page numbers occur
in the runtime rules. General context does not mean universal applicability.

On failed validation, the draft is kept locally, the answer is withheld, usage and
cost are still recorded, and there is no automatic retry. Missing periods and current
effectivity questions are withheld before a model call. Actual model calls require
explicit opt-in; pytest uses dummy credentials and mocked transport.

These are bounded source-fidelity checks, not a complete semantic entailment proof.
Rules do not understand every synonym, currency/entity restriction, cross-reference,
exception or prose layout. They cannot establish that a model's surrounding wording
is always logically entailed. `semantic_entailment_verified=false`, IR candidate
status and `facts_published=0` remain. Review real source and generated sentences in
the development E2E; do not claim independent generalization or financial validation.

Official Structured Outputs reference (read 2026-10-29):
https://developers.openai.com/api/docs/guides/structured-outputs
Schema adherence is distinct from the source-fidelity checks above. Model stays GPT-4.1.

Implementation results: `reports/sentence-rag-20261029/comparison.html` preserves
all nine graph runs, seven actual model calls (including blocked development drafts),
and the four final distinct questions. Hana's final answer cites one value and three
conditions; Gangdong cites the original value and unit separately; absent period and
current-effectivity questions abstain without API use. Historical JSON/IR is unchanged.

Observed failures drove output-contract changes: omitted conditions; labels without
sentences; general context mislabelled direct; unsupported universal wording around
an accurate quote. Each assessment now requires a sentence. Explicit input condition
IDs and lexical-scope hints improve compliance; validation still independently rejects
mismatches. A limited universal-wording guard was added after source review. This is
development iteration, not a held-out accuracy estimate or a proof against all paraphrases.
