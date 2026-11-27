# ADR-071: Last human verification boundary

Date: 2026-11-05. Status: partial implementation; promotion is intentionally unavailable.

The monitor's observation/re-review action is feedback to the model, not approval
of parser code or a confirmed reference. The selected LH page 12 request
`bf27b129db614440aa052d2b2e5aabfa` has no repair candidate at inspection time.

The same monitor now explains readiness and four eventual human judgments:
table geometry, row paths, column paths, and values/provenance. Existing baseline
and candidate preview controls remain available for attempts with actual outputs.
No accuracy score is inferred from changed cells, draft references, or model agreement.
The final confirmation control is disabled, including for passing legacy attempts.

Automatic and registered proposal execution stop at `awaiting_source_review` when
checks pass. `apply_to_workspace` cannot bypass this boundary. The legacy
`apply_verified` entry point rejects application until a separate, immutable,
candidate-bound human approval and promotion transaction is implemented. Existing
applied attempts and extracted IR are retained unchanged. No schema migration.

Still required: construct and hash a complete comparison packet (source, code,
outputs, tests, evaluation and reference revision); store four scoped judgments;
validate corrected references; re-score baseline/candidate against the same
reference; preserve regression history; atomically promote the exact approved
candidate with stale-version checks; resume the campaign on unused documents.
The new monitor is preparation guidance, not a working final-approval form.

Deployment consideration: Python workers already running must be restarted to
load the new application boundary. Static monitor assets reload independently.
Do not describe an old running worker as protected by edited source code.
