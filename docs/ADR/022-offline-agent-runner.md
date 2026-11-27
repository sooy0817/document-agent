# ADR-022 — Offline semantic Agent runner

Existing IR, proposal, description and approval contracts remain unchanged.
The new runner accepts EvidenceBundle only; it never reads review ledgers or corrected
Canonical proposals as model inputs. Independent and candidate-assisted share source
items and response schema; only original hashed StructuredTableIR candidates differ.

This stage implements a bounded provider protocol and mock provider only. Network
providers are deliberately unavailable. All mock output is marked synthetic/mock,
never actual Agent inference. A new run directory stores input, short-ID registry,
request, response, validation, timings, failure codes and usage (zero actual cost).
Atomic directory creation prevents overwriting runs. Credentials are not accepted.

The wire response omits provenance hashes, approvals and original evidence edits.
Python binds short evidence IDs and locally namespaces proposal IDs, then constructs
existing bundles and invokes existing validators. Missing coverage is rejected rather
than silently omitted. ABSTAIN must supply unresolved coverage and a reason.
Descriptions, qualifiers, scopes and operations remain separate typed objects.
Semantic prose truth is not proved by reference validation.

UI exports are separate cases marked MOCK, with separate review storage. No prior
review events or Gold gates are copied. Supplementary source review is not a Gold set.
Evaluation without independently reviewed, source-bound gold returns N/A; smoke
admissibility is never model accuracy.

Privacy projection masks numeric tokens (except explicit years/dates), contacts and
paths, keeps geometry, occupancy and context labels. It is not a complete PII detector:
human payload/privacy approval is mandatory before a future network adapter.
Token estimates are conservative UTF-8 byte upper bounds, not tokenizer measurements.
Oversize inputs fail rather than truncate. Live pricing/tokenization/strict-provider
schema compatibility must be checked before any real call.
