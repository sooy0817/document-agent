# ADR-027 — Existing CanonicalDocument assembly and derived PostgreSQL RAG

2026-10-22. Implementation decision under the user's October 29 demonstration scope.

Audit first: DocumentIR 1.0, PhysicalTableIR 2.1 and StructuredTableIR 2.0 are
implemented. pipeline_contracts.CanonicalDocument uses the legacy 1.0 table draft
and has no builder. Staging stores artifacts, not an integrated document. rag_eval
is an offline experiment and does not query PostgreSQL or call an answer model.

Keep DocumentIR and the physical/geometry contracts unchanged. Complete the
existing CanonicalDocument draft with additive optional fields: embedded original
DocumentIR and physical_tables, Content.table_id/reading_order/context_content_ids,
duplicate_of_table_ids, Section.reading_order/evidence_state, context_links and
reading_order_status. Accept existing StructuredTableIR 2.0 alongside legacy 1.0
in its existing table_structure/hierarchy/mapping fields. The draft version stays
explicitly draft; this is not a new RAG Document schema. No Canonical financial
facts are produced. The original IRs, their hashes and source bytes stay intact.

One JSON query exposes original evidence, body, sections, table Trees and values.
Numbered headings produce section candidates; geometry orders text and tables.
Text overlapping tables remains as source-preserving duplicate content and is
excluded only from duplicate search chunks. Ambiguous column reading order and
section membership stay unvalidated. General numbering can add inferred row
parents in a derived view, never overwrite the original geometry artifact. No
institution/file name, row label, expected value or question may drive repair.

staging.artifact accepts CanonicalDocument without changing staging schema.
An additive rag schema references that exact run/artifact and stores disposable
search entries derived exclusively from that JSON. PostgreSQL simple text search
plus Hangul substring terms is the dependency-free baseline; no claim of Korean
morphology, embeddings or measured retrieval accuracy. Values are rehydrated from
the stored CanonicalDocument, not from model-generated JSON or search text.

GPT-4.1 is user-selected. Text only, one bounded call per question, store=false,
no automatic retries or fallback. A model selects evidence IDs and verbatim values
under a strict schema. Deterministic membership/value checks and source rendering
prevent invented numbers/citations from reaching the answer. All semantic
relations are candidates and all outputs say this explicitly; model confidence
does not approve semantics. Persist request/response/usage without credentials.

HTML uses a bounded static HTML table adapter into the existing DocumentIR and
PhysicalTableIR contracts, including rowspan/colspan and source byte hashes.
HTML coordinates describe a documented synthetic layout, never browser pixels.
The existing geometry analyzer accepts optional header_rows/stub_columns hints
from explicit HTML th markup. This fixes full-width title rows generically and
does not change either frozen IR schema or the PDF analyzer's default behavior.
Unsupported nested/layout tables are isolated for review. External scripts are
not executed. Economic PDFs reuse the existing PDF engine; scans retain OCR_REQUIRED.

The RAG builder can derive a simple two-column table view from original native
spans only when ruled reconstruction yielded no value mappings, at least three
single numeric columns align, left labels are separate, and an explicit header
exists. Wrapped/interleaved labels, multiple numeric columns or conflicting
alignment abstain. Original physical files and source cells remain untouched.
The fallback does not pretend to solve complex borderless multi-level tables.
Source acquisition URL metadata is hash-bound in source-origin.json and exposed
through optional CanonicalDocument.source_uri; HTML page numbers are synthetic.

Search index v0.2 weights source table headers/row paths above adjacent prose.
Its version can be rebuilt transactionally from the same immutable JSON snapshot.

Batch execution is serial and bounded initially. Source hashes deduplicate,
per-document transactions isolate failure, append-only events record progress,
and explicit resume skips only verified successful fingerprints. A 500-item
inventory is not evidence of 500 successful ingestions or semantic accuracy.
