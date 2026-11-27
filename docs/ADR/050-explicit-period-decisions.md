# ADR-050: Explicit per-target period decisions and role separation

2026-10-30. Opt-in experiment; no IR, Canonical, DB or axis-path migration.

## Problem

ADR-049 v0.3 has no explicit not-needed proposal state. Unknown can be confused
with known absence. Definition cells and duration-label applications have been
mixed, and repeated evidence ID bookkeeping caused otherwise usable responses to
fail validation. Historical runs and errors remain immutable.

## Decision

Add `period-decisions/1.0` beside the legacy module. Each requested target has
exactly one `required`, `not_needed` or `insufficient_evidence` decision. Required
decisions have definition-cell IDs and duration-label IDs; the enclosing target ID
is the value application. These roles are disjoint. Insufficient decisions must
state what evidence is unavailable. They cannot assert relations at the same time.

Additional supporting IDs are optional; code derives the complete reference union.
That union is bookkeeping, not independent corroboration. Structural validation
cannot prove text meaning or completeness of visible context. A source-only Judge
independently produces the same contract. Identical frozen inventories compare old
and new candidates without another Judge call. Old unknowns stay unknown; omitted
targets are interpreted only according to the old explicit contract, with a labelled
migration view. Model agreement never authorizes facts or training.

Only the existing API adapter gains overridable schema/prompt/version attributes;
its legacy defaults and recorded responses are unchanged. LangGraph nodes remain
bounded to two calls per region, no automatic correction loop. Traces distinguish
contract validity from per-target model agreement. Raw evidence stays local.

## Evaluation

Development: saved iM and Hana source regions, including the unrelated iM target.
Compare fixed old responses and new responses against the same new inventory;
also retain the old inventory's score. This isolates judging variation from
candidate changes, but does not isolate prompting from sampling variation.
Freeze code/prompts before new Korean PDF inspection and inference. Save original
PDF, extraction, source-only reference draft, model outputs, failures, usage and
hashes separately. Synthetic missing-context/role-conflict tests supplement real
documents and are never described as independent PDF accuracy.

All references remain pending human review. No row/column hierarchy repairs,
global production enablement, bulk queue, database writes or RAG publication.
