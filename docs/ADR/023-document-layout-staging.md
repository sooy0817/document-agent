# ADR-023 — Local document staging and layout sidecar

Status: local MVP; original IR contracts unchanged.

DocumentLayoutProjection 0.1 references every original page/block, retains extraction
order separately from unresolved geometric reading order. Page containment is physical,
not inferred chapter membership. No title/section hierarchy is invented. Table internal
structures reuse PhysicalTableIR 2.1 and StructuredTableIR; shared text spans remain
references, never deletion.

Page diagnosis uses native character count, union of image rectangles and vector content.
Image-heavy pages are routed OCR_REQUIRED, including mixed pages with native text.
Sparse/vector-only pages remain REVIEW_REQUIRED. This is conservative routing, not a
validated scan classifier. EXTRACTED means native extraction completed, not semantic approval.

PostgreSQL staging contains immutable source versions and successful run artifacts.
Attempts persist before atomic artifact/page/block/relation insertion. Failed transaction
rolls back all derived records; failure remains in run history. Retrying a failed request
uses a fresh attempt key. Repeating a successful key is a no-op; new processing versions
need a new key. No Financial Fact or publication table is created.

Development transport uses installed Docker Compose psql, avoiding new dependencies.
It targets only the project's postgres service, verifies expected database, and uses
ON_ERROR_STOP. Alembic revision wraps the same DDL for future managed migration.
No original PDF bytes enter PostgreSQL; existing extraction run may retain its usual
local source snapshot. Source path and SHA-256 identify the original.

Future OCR writes a new DocumentIR/run, never edits native extraction. Engine version,
page coordinates, source hash and confidence belong to an OCR provenance sidecar
(DocumentIR currently only permits null confidence). Confidence requires calibration;
OCR_REQUIRED pages must not be counted as successful text extraction.
