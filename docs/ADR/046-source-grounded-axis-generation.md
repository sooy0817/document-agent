# ADR-046 — Source-grounded role/path generation in LangGraph

Date: 2026-10-29. Implemented as opt-in experiment, no default pipeline changes.

## Problem and decision

ADR-045 wraps a finite single-level geometry option selector. No correct option
means no possible model repair. A new, separate source-generation adapter sends
cell aliases, original text, boxes, spans, row/column indices, merge sizes,
appearance and an optional original crop. It sends no options, Gold answers or
institution-specific rules. GPT-4o-mini generates every cell's role and source-ID
paths, concise evidence references and reasons. Existing selection code is intact.

The experimental graph is inspect_source → judge_llm → validate_generated →
materialize_candidate. Explicit generation comparison calls each physically
eligible region once, including merged regions, up to the run budget. The 180-cell
input guard remains; failure/disabled runs preserve baselines and mark fallback.
This is not automatic routing in the production extraction/RAG graph.

## Validation and status boundaries

Hard checks: source membership, complete role inventory, source evidence coverage,
duplicate/self paths, conflicting parents, role/path consistency, physical usable
references, row/column coverage and order, and cross-fragment scope constraints.
Numeric row identifiers may have both value and row_label roles with their own
value mapping. Other conflicting roles reject links. Absence and unknown remain
distinct; neither is silently replaced with an invented label.

Structural consistency NEVER means semantic correctness. Original header support
checks run unchanged as a separate legacy_validation result. A new generated view
may contain geometrically plausible but semantically wrong paths: every output is
review_required, semantic_verified=false, facts_published=0. Unsupported headers
are explicitly flagged for review, not relabeled as independently supported.
No new candidate is published to Canonical, production DB or answer generation.

Generated views use existing StructuredTableIR fields, exact source values and
prefix-based node IDs. Rejected/omitted old value mappings retain their values with
empty paths, preventing stale baseline paths from appearing as generated results.
Full roles (including notes and value+row_label) are retained in the sidecar since
the existing scalar CellRole cannot represent every role combination.

## Model operations and learning

Default is offline; explicit --allow-external required. No automatic retries.
Raw response, source/prompt/image hashes, token usage, estimated cost and errors
are preserved in local immutable outputs. External tracing is disabled by default.
Source documents are data, not instructions. No keys in artifacts.

Review records start pending, corrected_structure=null, approved_for_training=false.
There is no training, fine-tuning, automatic feedback update or export of model
outputs as labels. A future dataset requires independent source review, corrections,
rights/privacy checks, and document/template-family-separated test data. No test
document should enter training or prompt examples while reporting independent scores.

## Experiment

Freeze code/prompt before live calls. Existing Hana, NH continued-table, DB2026 and
iM Rich documents are development/regression data, NOT fresh holdout. Compare
unchanged rules, saved option-selection outputs (where available), and direct
generation on the same source. Do not treat missing selection runs as score zero.
Keep all 35 pre-existing source-reviewed DB/iM targets, including 16 physically
mixed values, in overall coverage; also report the isolated subset of 19.
Separate raw generated accuracy, hard-checked candidate accuracy, unchanged legacy
gate acceptance, and output-view accuracy. Human-reviewed correctness must never
be inferred from these automated gate statuses.

References: https://developers.openai.com/api/docs/guides/structured-outputs
and https://developers.openai.com/api/docs/models/gpt-4o-mini .

## Actual results and reproducibility

Report: reports/axis-generation-20261029/run-02/comparison.html.
v1 completed six source-region calls after one local network failure. On the 35
existing DB/iM targets: 6 raw correct paths, only 3/35 after hard checks (3/19
isolated values), versus baseline 5/35. Three correct DB paths were rejected for
missing repeated evidence IDs; this is a contract-compliance error, not an incorrect
semantic path. Hana direct v1 was 0/21 versus existing selection 21/21.

All v1 code was snapshotted and verified in run-01/frozen-source before revising.
explicit-v2 adds only generic path-contract instructions (exclude target itself;
use actual headers; include source references), no institution names or answers.
The validator was unchanged. Two additional development calls: iM p1 7/11 exact
versus baseline 0/11, but 4 wrong inner-table paths also passed geometry checks.
They dropped the row parent and used the outer Content heading. Hana recovered
3/21; internal rate paths remained worse than the existing option selector.
These results DO NOT justify replacing existing RAG mappings or claiming
generalization. No independent holdout was evaluated in this change.

8 completed API calls, 9 attempts including one URLError with unknown usage.
Known-usage estimated cost $0.05005935 (cached-token discounts not applied).
119 tests passed in 27.59s. Original value/reference checks passed; prior ADR-044
runtime hashes unchanged. Gangdong skipped model inference due to the 180-cell
input bound and retained its baseline exactly; this is preservation, not new
generation accuracy. No Canonical/DB writes, fact publication or training.

Example explicit run (output must not exist):

```powershell
.venv/Scripts/python.exe scripts/run_axis_generation.py --source reports/axis-validation-20261029/run-01/final/hana-bounded --output reports/axis-generation-new --allow-external --vision --max-calls 1 --prompt-version explicit-v2 --env-file .env
```

Without --allow-external, the graph stores source/baseline fallback only. Replay
requires an existing generation run with an identical source hash; it is not
interchangeable with old option-selection responses.
