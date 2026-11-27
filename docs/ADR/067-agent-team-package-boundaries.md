# ADR-067: Agent teams and application boundaries

Date: 2026-11-04

## Decision

Retire the root `demo/` package. It contains application code, model roles, persisted
queues, tests and browser assets that now have independent responsibilities.

- `agents/`: role prompts and typed output contracts.
- `teams/table_review/`: role classification, relation interpretation, independent
  judgment, validation, and the queue-facing service. Keep classification output
  out of the relation analyst and Judge inputs.
- `teams/evidence_review/`: source-first review and at most one evidence expansion.
- `teams/legacy_review/`: retained execution for persisted `agents` requests.
- `teams/axis_review/`: source-axis review used by evaluation clients.
- `tools/`: read-only evidence selection, expansion and stored relation lookup.
- `validation/`: deterministic source, quote, coverage and topology checks.
- `runtime/`: model transport, durable call budget, configuration, artifacts and queue.
- `review/`: append-only user feedback and reference records.
- `web/`: Folio HTTP application, document views and static assets.
- `parser_experiments/`: isolated candidate code experiments, never parser promotion.

The graphs remain bounded LangGraph workflows. This refactor does not introduce an
unbounded supervisor, additional model calls, automatic financial-fact publication,
or automatic activation of paid providers. Prompt text, model/profile versions,
request hashes, result formats, queue modes and existing storage paths are preserved.
Existing `api/` and `human_review/` contracts remain separate entry points; their
behavior is not silently replaced by the Folio UI.

## Migration

Update all active source, test, script and startup references. Move former demo tests
into the normal `tests/` collection and extract shared fixtures. Package static
assets and expose `python -m doc_understanding.web` as the application entry point.
Update the isolated experiment worker and source snapshot to the new package paths.
Keep historical reports and immutable experiment snapshots unchanged.

Do not delete old workflows solely because their names are old: persisted queue
requests still dispatch to them. Remove duplicate source copies and fallback imports
only after their callers have migrated and regression tests pass.

## Verification

Verify mock-provider role calls, source immutability, one-expansion bounds, quote and
coverage rejection, cached replay with no new calls, queue cancellation/recovery,
human decisions, model settings, UI routes/static assets, package imports and the
parser experiment worker. No paid API or production database checks are required.
Record actual commands/results in the implementation report; test passes are not
semantic accuracy measurements.
