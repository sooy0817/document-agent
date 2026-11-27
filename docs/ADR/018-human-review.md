# ADR-018 — Local human review with separate Gold and publication gates

Status: implemented local review workflow; not production authorization.

Preserve immutable Canonical input and record append-only review events in a separate
local JSON ledger. Each review records source/proposal hashes, reviewer (self-declared
local identity), timestamp, selected evidence, before/after full structure and local
validation. Relations can be corrected or removed without changing source cells.
Review scope is explicit: one relation or the whole structure. Relation-scoped Gold
candidates do not certify the rest of a table. New revisions invalidate current gate
statuses but preserve prior events.

A review save is not approval. Candidate approval, Gold finalization and operational
publication authorization are distinct events tied to the exact review revision.
Gold finalization requires a different reviewer; operational authorization is an
explicit further decision. No endpoint publishes facts or writes a production DB.
Local identity entry is not authentication; production use needs authenticated roles.

Bind server to loopback. Allowlist case files and rendered source images, validate
Origin for writes, use optimistic revision checks and atomic ledger replacement.
Single-process server only. Source artifacts are hash-checked; an altered source
blocks review writes. PDF text is untrusted, never executable markup.

Initial cases are Architecture draft design examples, clearly labeled as such.
The same loader accepts CanonicalSnapshot agent_execution bundles later. No new
Agent or LLM execution occurs. Existing frozen IR contracts remain unchanged.

## Numerical-first review refinement

The default screen now shows recomputed existing scope checks and numerical exceptions.
It does not infer a scope by searching for a sum. The advanced relation editor remains
at /advanced. Historical provisional annotations and current contract-design scopes
are reported separately. Exact mismatch remains a mismatch even when a separately
recorded rounding hypothesis explains it. No numerical pass promotes any Gold gate.

Approvals for unchanged relations can accumulate. Any structural correction
conservatively invalidates active approvals while preserving every event.
