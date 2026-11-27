# ADR-075: Explicit inputs for domain-independent execution

Date: 2026-11-09. Status: implemented locally; see the audit for validation limits.

## Problem

The physical parser was reusable, but runtime consumers still encoded NH entity
suffixes, a 2025 disclosure directory, specific institutions, pilot documents,
historical review catalogs and a single RAG model. A text-only HTML table also
lost explicit header evidence during semantic inference.

## Decision

- Remove institution suffix recognition. Use caller-required source names and
  exact original-question-anchored source entities from an optional question plan.
  `entities` is additive; `institution` remains readable for older plans. Each
  required source must have evidence; unrelated sources are removed before answers.
  This is a lexical source-name guard, not verified issuer metadata or general NER.
  With neither a plan nor explicit sources, identity mode makes no source-name
  verification claim. Guessed names never become constraints.
- All answer modes abstain on evidence validation failure. Agent mode retains one
  bounded original-question retry. Period labels may appear on either table axis;
  the legacy `REQUESTED_PERIOD_NOT_IN_COLUMNS` code remains for compatibility.
- Expose evidence limits and RAG model/cost assumptions. Unknown models need
  explicit input/output rates. Rates are caller assumptions, not live prices.
  One Responses API call remains the transport contract; other provider protocols
  and structured-output compatibility are not automatically implemented.
- Rank eligible evidence using original cell text as well as row/column labels.
  Preserve all raw values and unresolved lookup restrictions.
- Honor explicitly supplied source-markup header/stub hints. Do not apply the
  missing-geometric-header ambiguity to an already supplied layout. PDF inference
  without these hints keeps its existing conservative gate. Version is
  `semantic-geometry/0.3.1`; existing versions remain readable. No physical IR or
  saved baseline is rewritten, and no meaning is marked human-validated.
- Batch inventory receives an input root, glob and explicit path/hash exclusions.
  Unseen sampling receives excluded names/IDs in the freeze policy, and continues
  to propagate prior exposure and duplicate components. Legacy corpus exclusions
  must be passed explicitly when reproducing historical experiments.
- Sample/public-source catalogs are opt-in local JSON. No institution or specific
  pilot is chosen at startup. Campaign CLI requires a manifest; the web workflow
  uses an explicitly configured manifest or the local inbox. Source download
  policies retain same-host HTTPS, no redirects, size and provenance checks.
- Review app inputs (cases, source catalog, precheck, semantic review pack and
  guidance) are explicit. The landing page is generic structure review; optional
  arithmetic inspection lives at `/arithmetic`. No historical report is an
  implicit fallback. Mock visibility follows stored origin, not a filename prefix.

## Boundaries

No database migration, re-indexing, remote calls, deployment or source PDF upload
is required for this cleanup. Historical outputs and tests remain evidence; they
are not production defaults. `.agent/` is preserved. Financial metadata fields in
stored contracts remain optional/unknown-compatible to avoid destructive changes
to prior artifacts. This task does not change frozen physical behavior or KB artifacts. A concurrent
folder reorganization relocated physical modules; the separate freeze checker
verifies import-normalized AST equivalence and byte-exact baseline artifacts.

Pinned semantic-review and isolated repair model protocols remain specialized
adapters, not arbitrary-provider support. This change does not implement vector
retrieval, universal entity recognition, every PDF layout, OCR, semantic approval,
or complete all-table text recall. See [audit](../DOMAIN_ASSUMPTIONS_AUDIT.md).
