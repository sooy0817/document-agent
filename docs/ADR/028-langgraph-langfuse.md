# ADR-028 — Wrap existing Table RAG in LangGraph; isolate local Langfuse

2026-10-22. User-authorized implementation, first phases 1–3, then bounded phase 4.

No DocumentIR, PhysicalTableIR, StructuredTableIR or CanonicalDocument change.
Keep PostgreSQL queries/scoring, evidence_pack, answer.generate/check_selection/
render_answer. Extract the existing CLI's multi-document evidence loop into one
shared service used by both the old CLI and graph; preserve ordering and limits.

The initial graph is START → understand_question (identity) → retrieve →
resolve_evidence → validate_evidence → generate_answer → END. State retains the
original question, search request, ranked candidates, evidence, structural integrity
checks, answer, errors and a local execution history. Semantic status stays candidate.
Validation checks references/raw values; it never approves financial semantics.

Tracing is optional and fail-open. One SDK application-root observation owns one
request trace. The official Langfuse LangGraph CallbackHandler creates graph/node
observations; explicit PostgreSQL search/document-fetch/cell-lookup and GPT-4.1
generation observations nest under the active node. No second callback/provider
auto-instrumentation duplicates the GPT request. SDK export-stage masking projects
callback State to bounded, redacted data and forbids raw documents/SQL/credentials.
An OTEL exporter wrapper removes SDK instrumentation-scope attributes (including
the project's public key), events and links after SDK masking. Authentication
headers are transport-only. The wrapper uses public OTEL interfaces and the
official SDK's supported span_exporter constructor argument.
The local request audit retains authoritative original input. Traces retain the
original question except personal/credential redaction. SDK errors never rerun or
abort RAG. The RAG function never waits for flush; the demonstration CLI explicitly
flushes after generation. SDK HTTP timeout is two seconds; SDK background export
may retry and log failures. A generated trace ID is not a delivery acknowledgement:
only Observations API read-back confirms persistence.

Local Langfuse runs in a distinct Compose project with its own PostgreSQL, Redis,
ClickHouse, MinIO, network and volumes. Only loopback UI/storage ports are exposed.
No existing NH DB credentials/volumes/services are shared. Telemetry/AI features
are disabled. Random headless-init credentials are stored only in ignored files.

The user subsequently provided a separate project in an ignored client.env.local.
Support explicitly selected env configuration in addition to JSON, including HTTPS
remote destinations; no automatic cloud fallback. Their existing localhost:3000
server uses v3 write mode. Actual OTEL ingestion and legacy REST trace read-back
work there; use a read-only legacy fallback for v2 API 404, without migrating that
server. Keep the isolated latest v4 environment and document tested compatibility.

After equivalence is actually recorded, agent mode may use a strict GPT-4.1
question-plan response: intent, institution/year/metric candidates, query type,
unverified proposed filters and rewritten query. These are hypotheses, not verified
metadata predicates. Baseline identity mode remains the default. A plan never adds
unverified SQL filters; explicit caller run_id is preserved. Insufficient/period-
missing evidence permits at most one retry using the EXACT original question,
never relaxing a caller constraint or fabricating a year. Interpret failures also
fall back to the original question. Both attempts and reasons are traced.

Ordinary pytest: fake API key, external network blocked, mocked providers. Opt-in
local PostgreSQL tests still mock GPT. Live calls require separate --allow-external
commands and persist request/response/usage/cost. Comparison distinguishes exact
deterministic retrieval/evidence equality, saved-response replay equality and
independent live model outcomes; no claim that two stochastic calls always match.
