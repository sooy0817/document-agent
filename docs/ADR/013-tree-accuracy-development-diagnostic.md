# ADR-013 — Source-authored Tree diagnostic, isolated from production IR

Namwon is fixed development/regression data. New manual reference cells use their own
IDs and image-measured boxes, never prediction IDs as annotation identity. Handwritten
row groups, header paths, spans and aggregation scopes are stored in an evaluation
fixture. Source PDF native words only populate text inside annotated boxes. Predictions
are subsequently bound by bbox IoU >= 0.7, with ambiguity/many-to-one rejection.
Whitespace-only normalization is allowed for label/text comparisons; numeric values,
symbols and blank-vs-zero distinctions are not normalized away.

Tree annotations remain provisional pending human review. Previous 25-question
confirmation does not approve this larger graph. Row membership of a subtotal is
distinct from its aggregation members. Ratios are not additive. Biological assets
show no underlying detail in this selected table, so the missing scope is unknown.

Rule and stored live-response C1.1 replay are evaluated on the same reference.
Common-table metrics must accompany method-specific coverage. Context-v1 prepared
requests are not executed outputs. Missing typed aggregation/cross-page predictions
have coverage gaps and N/A accuracy; no invented success/failure rates.

The HTML is a local read-only comparison artifact. All new contracts live in evaluation
files; DocumentIR, PhysicalTableIR, StructuredTableIR and Semantic Overlay stay frozen.
No RAG/LLM calls, database changes, deployment, or Nampyeong access.
