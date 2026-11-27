# ADR-066: Feedback-linked parser code experiments and honest cost reporting

## Decision

Human feedback may now request actual parser code changes, superseding the earlier
review-only product scope. Keep the original review queue unchanged and attach a
separate durable experiment queue. Each feedback revision is deduplicated, attempts
have separate folders, and events remain append-only. No production database, Git
push, source PDF, existing extraction or financial fact is changed.

The adapter can request an exact replacement patch from a tool-free Claude CLI.
The patch may touch only enumerated parser implementation modules, not tests,
contracts or validation controls. Syntax and unique replacement context are checked
before writing the isolated candidate. Proposed code runs only in a network-disabled,
read-only Docker container with a dedicated writable output directory, no host secrets
and no Docker socket mount. The existing fixed tests and PDF inputs are frozen first.

The workflow records baseline/candidate test exits and raw extraction differences.
Cell counts, changes and path availability are diagnostics, not correctness. A passing
run stops at source review; promotion to the default parser is NOT implemented.
Holdout status is not inferred from a newly uploaded file. Dataset registry entries
are hash checked; regression and evaluation inputs are not sent to the code generator.
At most five additional PDFs are processed per run. Automatic online acquisition and
independent Gold scoring remain separate future work.

## Costs and current activation

Claude is optional; parsing, regression, metrics and report rendering require no
model. A paid adapter must not be confused with the deterministic worker. Automatic
paid code generation is OFF following the user's cost feedback. No new model call
occurs merely because the dashboard is opened.

The first large request unnecessarily serialized duplicate physical/structured IRs.
The second attempt returned `error_max_budget_usd`; its reported equivalent cost
was $2.9542634. The CLI budget is a stop criterion, NOT a hard billing ceiling.
The first attempt's usage was not captured and must remain unknown, not zero.
The compact Sonnet attempt returned an invalid empty/test proposal and was rejected;
its reported equivalent cost was $0.314716. A separate connectivity check reported
$0.011131. These are CLI list-price estimates, not verified subscription charges.

The compact packet uses selected-page physical cells, nesting evidence and three
relevant source modules. It rejects inputs exceeding 120,000 characters. Generated
code is not trusted as Gold and no valid parser fix has been established by these
model attempts. The dashboard must show these failures rather than an improvement.

## Review UI

Reuse the existing monitor and source viewer. Show:

- known estimated cost per invocation, plus explicit missing usage;
- observed workflow events, changed files and reasons when available;
- same-PDF baseline/candidate source viewer links;
- source text preservation, changed cells and nesting deltas by dataset role;
- explicit pending semantic correctness and generalization status.

Do not draw an accuracy trend before comparable human-referenced results exist.
Control charts, confidence intervals and ROC curves require appropriate samples,
labels and repeated measures; they are not manufactured from model agreement.
