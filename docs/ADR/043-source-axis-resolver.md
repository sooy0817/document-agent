# ADR-043: Opt-in logical-region axis resolver

2026-10-29. Experimental; no automatic semantic verification or financial publication.

Preserve DocumentIR, PhysicalTableIR, StructuredTableIR and CanonicalDocument bytes.
Add an independently stored axis-resolution sidecar. The opt-in runner reads an existing
staging run, groups physical fragments only within a unique native table container,
retains every input cell and proposes ID-only row/column relationships. Physical and
native IDs are not interchangeable. Malformed or source-uncovered cells are withheld.

Rules propose flat numeric matrices and appearance-supported aligned fragments.
Text and vision adapters receive the same cell evidence; vision additionally receives a
source crop including the whole logical region. Neither provider receives evaluation Gold.
Model outputs never modify source text, values or geometry. Unknown IDs, role conflicts,
bad geometry, malformed cells and absent axes without independent proof remain unresolved.
Supported candidates still have semantic_verified=false and are not wired into fact
publication or production RAG. Existing nested-content sidecars supply parent/prose refs.

The experiment compares baseline, rules, text and vision separately. Do not claim this
implements general page continuation, arbitrary region segmentation, physical repairs,
or semantic truth validation. No default parser/DB behavior is changed.

Development revision: unrestricted path proposals failed on the development page.
Preserve those calls. The frozen provider instead selects/rejects finite geometry options
with an exact option partition. This limits the pilot to single-level paths; multi-level
baseline paths remain unchanged. Candidate recall is evaluated separately from selection.

Frozen experiment outcome: reports/axis-resolver-20261029/run-01/comparison.html.
Hana development targets: baseline/rules 18 of 27 exact value-and-path matches;
text/vision 21 of 27. Six physically malformed values remain unresolved. New NH1418
source was acquired and reviewed after the implementation freeze; 42 predeclared
value entries across pages 1/4/6 yielded 13 exact matches in all modes. All 42 source
strings were retained (whitespace-normalized reference comparison). On the continued
outer table outside that subset, vision incorrectly selected three links that used a
body paragraph as the column header; geometry checks passed. Text rejected those
same options. This is evidence against automatic vision adoption or treating
supported_candidate as semantic correctness. No runtime/prompt revision followed
holdout inspection. The default parser, CanonicalDocument and production RAG remain
unchanged. Further absent-axis/continuation work requires a new development cycle.
