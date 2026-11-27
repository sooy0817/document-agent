# ADR-008 — Rule-guided Text Semantic Overlay / Phase 2-C1

2026-10-16. Scope authorized by the user's pasted Phase 2-C1 request.

PhysicalTableIR 2.1, StructuredTableIR 2.0, DocumentIR 1.0 remain unchanged.
New `semantic_resolver` package consumes all three and creates a separate immutable
SemanticOverlay 1.0, feature index and ID-only SemanticPatchProposal 1.0.
Source snapshots, input hashes, raw strings, blank/zero distinction and physical
evidence tiers remain intact. No facts, arithmetic normalization, templates or Vision.

Feature nodes reference existing source nodes/cells/spans. Repeated header patterns
and numeric/text column distribution propose independent panels. Text outside table
regions gets a separate document hierarchy. Number marker families are interpreted
by the local sequence stack, not a hardcoded Roman/Arabic/Korean depth dictionary.
Containment, indentation and marker candidates retain evidence and conflicts.
Parenthesized labels may be adjustment/alternative/annotation edges, not values.

Accepted means mechanically admissible, source-backed overlay relation, never
independently verified semantic truth. Text-only/inferred/cross-page/ambiguous
relations remain review_required. Original physical `inferred` is not promoted.
Proposed and rejected patches remain in the audit trail; no source IR is mutated.
Cycles, duplicate parents, cross-panel/document links, order/scope/evidence mismatches
are blocked. Review candidate paths are displayed separately from accepted paths.

Provider Protocol + Mock + OpenAI Responses text-only REST adapter. No SDK required.
Model is explicit configuration; timeout/retry/concurrency/call/input/output/run
budgets are bounded. External calls default disabled. Transport logs expose only
hashes, timing, provider usage, estimated cost with configured prices, not secrets.
Rule-only and Mock first. Export a bounded request manifest before any authorized
real call; never upload source PDFs or raster images. Refusal/failure/truncation
leaves review required. LLM-only omits rule candidates for comparison; validator
and physical constraints still apply. Mock success is not real LLM quality.

Evaluation separates source-image/user-authored selected expectations from outputs,
development from frozen evaluation documents, and real from non-financial synthetic
data. Unannotated full precision/recall and model uplift are N/A. Humans have not
independently approved these goldens. Split/hash manifest is frozen before resolver
development; this is not a blinded independent benchmark.
