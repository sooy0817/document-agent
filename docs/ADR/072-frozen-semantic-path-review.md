# ADR-072: Frozen physical IR, separate semantic path review

Date: 2026-11-05. Status: implemented comparison service; candidate semantics only.

## Direction

Prioritize row meaning and column meaning over further physical parser repair.
Keep PhysicalTableIR 2.1, saved jobs, text, geometry and cell identities unchanged.
The user deferred the LH page 12 detached-header case; this workflow excludes
detached-header links, including page continuations depending on such a link.

## Evidence and first implementation

The saved LH page 31 analysis treats the leading 11-row merged classification
as 11 header rows and produces four body mappings. Current existing
`logical_tables.build_logical_links` can already propose page 30 header inheritance
and multi-level row paths on the frozen input. This is a missing comparison and
delivery step, not evidence that a new inference algorithm improved accuracy.

`services/semantic_path_review.py` builds a typed immutable comparison packet,
HTML with embedded original page images, and a manifest recording source PDF,
document, physical IR and implementation hashes. It reads the current snapshot,
uses the existing logical candidate builder, and never invokes extraction,
Docker, an LLM, or code promotion. Existing saved paths are compared by source
cell IDs. Unresolved physical cells/boundaries and omitted uncertain intermediate
row groups keep candidate paths unresolved. Every output directory is new.

The comparison includes ALL selected physical cells, not only known data cells.
Its before/after both-axis coverage uses the SAME candidate-bearing cell set.
Coverage is not accuracy. `accuracy_score=null`, `semantic_verified=false`.
No confirmed reference, score, human approval, or publication is manufactured.
No default parser or monitor job result is replaced. No DB migration.

## Next semantic work

1. Diagnose role selection independently of hierarchy: a large row group must not
   silently become a header; preserve competing layouts when evidence is weak.
2. Row paths: preserve all intermediate groups, repeated labels scoped by parent,
   row-spanning scopes, and separation of totals/notes from parent-child edges.
3. Column paths: multi-level headers, period/unit scope and cross-page inheritance
   only with source-supported continuation; alignment alone is insufficient.
4. Independent references and negative examples: exact full-path match, parent-edge
   precision/recall, wrong-link rate and unresolved rate; same frozen reference for
   baseline/candidate. Report coverage separately. Unseen documents stay held out.
5. Connect the packet to final human judgment, re-scoring and the campaign graph.
   Final parser promotion remains disabled under ADR-071. Monitor integration is
   not implemented by this service; current monitor still shows saved job outputs.

## Validation

`test_semantic_path_review.py`, `test_logical_table_links.py`,
`test_semantic_generic.py`: 32 passed, including local frozen PDF and synthetic
negative cases. Source text is escaped in HTML. Repeated packet builds are
deterministic; physical IR is unchanged. This is not LLM validation or human Gold.

Actual LH page 31 comparison: 43 source cells, 43 candidates, 4 to 41 cells with
both-axis paths, 2 unresolved candidates. These counts include classification
cells and must not be reported as a 95% accuracy score.
