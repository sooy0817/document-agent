# ADR-010 — Semantic repair sidecar / offline C1.1

Status: implemented locally, experimental; not fact approval.

DocumentIR, PhysicalTableIR 2.1, StructuredTableIR and SemanticOverlay 1.0 remain unchanged.
New versioned contracts live in semantic_resolver/repair.py. Existing live adapter is not rewired.
Request-local deterministic N001 aliases bind to exact original nodes and evidence; a registry hash
prevents cross-request reuse. No fuzzy matching, label-based correction, or invented source IDs.
Evidence binding identifies provenance, not evidence that a semantic judgment is true.

Pipeline: frozen features -> alias registry -> short-ID proposal -> exact binder -> existing
structural validator -> ADD/REPLACE/ABSTAIN transaction -> revision + coverage + aggregation sidecar.
REPLACE requires the old parent as a compare-and-swap precondition. ADD cannot overwrite.
ABSTAIN preserves the previous result and marks coverage explicitly. Conflicting operations on one
child are rejected, never last-write-wins. Revisions retain before/after and operation history.
Only parent_child mutates the hierarchy. Other legacy relation types remain auxiliary.
All applied changes are review_required; physical evidence states are copied, never promoted.

Aggregation scope is a separate membership object, with subtotal_of / total_of edges pointing from
aggregate node to scope. No implicit conversion from existing parent_child, no arithmetic inference.
Membership is explicit, disjoint from exclusions; cross-scope/document/page and cycles are checked.

Replay distinguishes unchanged historical responses from counterfactual exact-endpoint rebinding.
The latter cannot measure how a future model will perform with aliases. Unknown IDs stay rejected.
Raw semantic agreement is scored separately from admissibility and final application. Provisional
Namwon 14-edge gold is development-only. Nampyeong remains unopened/unscored holdout.

Compatibility: new sidecar consumers opt in to version 1.0. No migration of frozen original artifacts.
CLI is offline-only and takes a prepared evaluation directory; no provider calls. Future live wiring
and fresh holdout runs require separate authorization, frozen prompts and independent gold review.
