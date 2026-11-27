# ADR-033: Conditional, source-bound nested cell content

2026-10-26 — implemented as a local, deterministic post-assembly stage.

CanonicalDocument, DocumentIR, PhysicalTableIR 2.1 and StructuredTableIR are
unchanged. A `nested-cell-content/1.0` sidecar binds to the canonical snapshot's
normalized SHA-256. It references existing native parent cells, native child
table candidates, physical tables, Content IDs and span IDs. This is a geometric
containment overlay, not a semantic approval or replacement document schema.

All pages receive a lightweight candidate scan. Only cells that strictly contain
a smaller, independent table receive ordered-content assembly. Coordinate
tolerance is 1.5 PDF points; an equal-sized duplicate is not a nested table.
An innermost unambiguous owner is selected; competing owners are retained as
unresolved. Native candidates without a corresponding physical table remain
visible failures. Parent/child span evidence is retained and checked.

Inside each selected parent, child tables are atomic sequence items. Original
Content lines outside those tables are referenced in coordinate order. Shared,
uncovered or conflicting spans, overlapping/same-height items, and unavailable
physical children prevent a resolved sequence. Whitespace is explicitly retained
in the coverage ledger. Original text is never cut or regenerated. The parent
cell's original flattened text remains intact in DocumentIR. Child tables retain
their own Row/Column Trees and unresolved semantic mappings.

`build` emits a companion sidecar; `link-nested` handles an existing snapshot.
`resolve_cell_content` hydrates parent and ordered items from the original JSON.
No automatic DB migration, new embedding index, LLM, cross-page inference or
financial fact publication is introduced. Source extraction omissions remain
possible: no candidate found does not prove that the PDF contains no nesting.

Validation: synthetic negative/ambiguity cases, unchanged legacy RAG/physical
tests, and an isolated full LH physical/semantic/assembly rerun. LH is development
data. Page-specific reference sequences are evaluation-only, never parser input.
