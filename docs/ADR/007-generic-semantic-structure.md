# ADR-007 — Generic deterministic semantic structure (Phase 2-B)

Status: implemented contract decision, 2026-10-16. User authorizes Phase 2-B.

PhysicalTableIR 2.1 remains byte-for-byte unchanged. The former unimplemented
StructuredTableIR 1.0 draft is retained as a legacy contract; new consumers use
semantic_models.StructuredTableIR 2.0. No migration of physical evidence is needed.
Semantic CLI exports the new schema separately. No template, fact, or LLM execution.

Hierarchy decisions use only rectangular slot containment and relative position.
Labels are opaque payloads, including digits and years. A corner spanning header
rows/label columns supplies a layout hypothesis, never semantic certainty. Leading
body merges may extend the stub hypothesis. Unsupported layouts retain ambiguities.
Each relationship is inferred even when its physical cell is observed; uncertain
physical evidence propagates as unresolved. Original boundary records are embedded
unchanged. There is no automatic promotion or human-validated output state.

Nodes retain half-open row/column ranges, physical IDs and boxes. Paths use IDs,
not labels. Subrows lacking a unique row label stay unresolved rather than receiving
invented semantic labels. All cells are accounted for in roles, including corner,
unknown and blank cells. Side-by-side and continuation are candidates, never joins.

Evaluation separates manually authored synthetic geometry expectations from real
PDF assertions reviewed by the coding agent. Neither proves unseen-PDF accuracy.
Semantic goldens remain human-review-pending. Label permutation is a metamorphic
test; it does not certify the initial layout hypothesis. CLI accepts arbitrary
PhysicalTableIR files or a new PDF without source-code changes.

Frozen physical model SHA256: 71495ee04130eceab908f0d9a241224882d733f515a375aac3f2c59e88ab1c3f

Frozen physical schema SHA256: ee1e0eb8c246328c96a25f7a9bc187d90426602a188b224b8e240625de99c476
