# ADR 015 Merged row mapping and uncertain parent quarantine

Opt-in candidate view only. Existing DocumentIR, PhysicalTableIR, StructuredTableIR and Semantic Overlay schemas and saved inputs remain unchanged.

Add a same-row label candidate when a unique leftmost one-row merged text cell has cells to its right and the value mapping lacks a row path. Preserve all source values, boxes, boundary states and unresolved status.

Quarantine parents outside the current table and same-column populated one-row parents that lack geometric containment. This is abstention, not proof of a root. Preserve previous edges in a separate trace. It can suppress valid semantic relationships, so report quarantine counts and never equate an absent edge with an independently validated root. Multi-panel/multi-stub layouts abstain.

Evaluate the candidate view on Namwon before freezing it for new institution smoke evaluation. Residual scalar-row interpretation and reference geometry require further review; do not edit gold to inflate accuracy.
