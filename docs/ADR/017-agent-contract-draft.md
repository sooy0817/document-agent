# ADR-017 — Agent interpretation contracts 0.1 draft

Status: draft, local contract implementation only; not Architecture v1.0 approval.

## Decision

Add an isolated `agent_contracts` package. DocumentIR, PhysicalTableIR 2.1,
StructuredTableIR 2.0, Semantic Overlay and Aggregation Sidecar remain immutable.
No production parser, resolver, provider, retrieval or DB path adopts these models.

EvidenceBundle carries source artifacts and source evidence separately from optional
Rule candidates. Independent interpretation forbids candidate artifacts in its
input. Candidate-assisted interpretation records the candidate snapshot and hash.
Both modes may propose new source-anchored logical nodes and new relations, not
only choose existing Rule edges. No proposal may generate physical cells or values.

StructureProposalBundle represents logical nodes, relation claims, tree projections,
cell bindings, scopes, typed operations, alternatives and coverage. Relations are
CHILD_OF, AGGREGATES, DERIVED_FROM, REFERS_TO, APPLIES_TO, CONTINUES_AS. N-ary
operations carry operands, roles, signs and explicit policy. Qualifiers preserve
period, unit, measure and record context without adding domain-specific edges.

ValidationResult separates source, topology, binding, operation, scope, semantic
and human-review checks. Local checks establish admissibility only. CanonicalSnapshot
preserves proposals and checks without publishing financial facts. RAGProjection
stores retrieval descriptions and references, not copies of numeric value cells.

## Compatibility and limitations

Existing StructuredTableIR fixes its analyzer version to geometry; Agent output
must not masquerade as this analyzer. A future projection adapter requires separate
compatibility review. Existing ID Binder and coverage/aggregation checks inform the
draft but are not silently applied to unrelated relation types.

Original boundary evidence states are preserved through hash-addressed physical
artifacts; local projections retain exact source cell IDs, text, coordinates and
occupancy. Cross-file integrity requires artifact hash verification as well as model
validation. Schema-valid does not mean PDF-correct, semantically true or exhaustive.

The 26 initial regions plus 13 additional regions and one non-table control are
design cases, not Agent predictions. New cases lacking PhysicalTableIR remain
context-only and cannot claim validated cell-level transfer. Nonfinancial real-PDF
generalization, model accuracy and RAG answer quality remain unevaluated.

## Next gate

Review endpoint ontology, record/condition representation, semantic approval policy,
period/unit normalization, operation execution/tolerance and candidate-assisted
anchoring bias before production integration. No new external calls are authorized.
