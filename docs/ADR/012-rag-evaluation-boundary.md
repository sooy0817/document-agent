# ADR-012: Read-only structure-aware RAG development experiment

Status: accepted for offline preparation; external model execution not authorized.

The priority is now downstream retrieval and QA quality, not database loading.
Add `rag_eval` adapters, local corpus artifacts, reference questions, vector-import
retrieval, metrics, and a review interface. Existing IR and Semantic Overlay remain
unchanged and are hashed before/after artifact generation. No existing parser rules
are tuned to these questions. Namwon is development data; Nampyeong is not opened.

The row/column trees encode physical hierarchy. Semantic edges are separate
proposals; subtotal/total scopes are not silently converted into parent-child edges.
Current chunking includes physical paths and semantic parent paths. Aggregation
Sidecar and cross-page note links are not yet indexed as explicit edges: an ablation
must evaluate them separately before deployment. Native page context is included
in B/C/D without inferring the scope of a note.

Reference questions were authored by an agent inspecting original page renders.
They are provisional, not an independent human Gold Tree. Partial reference paths
are review material only, never search corpus inputs. Rule and stored actual Hybrid
paths are exported independently. Missing Hybrid pages are omitted explicitly.

No provider client is added. The offline CLI accepts vectors with matching corpus
and question hashes and saved answers. A future approved runner must enforce model,
token, request and dollar limits before each call; no automatic model fallback.
It must bind short citation IDs to original evidence and log usage without secrets.

This experiment is not a PostgreSQL migration, a Fact publisher, or evidence that
tree retrieval improves accuracy. Only paired runs against reviewed evidence can
support that conclusion.
