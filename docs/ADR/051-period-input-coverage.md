# ADR-051: Source context audit before accepting absence

2026-10-30. Opt-in period sidecar guard. Source IR, axis paths and production RAG unchanged.

The two ISA false-absence outputs agree with each other despite receiving only
inner table crops. Prompt text already says missing context is not absence. The
input does not identify omitted parent prose, so another prompt alone cannot
provide that evidence.

Compare actual request span IDs against complete DocumentIR. Per target, inspect
native parent cells using existing strict containment (1.5pt tolerance and <95%
area) plus shared spans. Inspect native row siblings for omitted local labels.
Adjacent pages are screened only when the target's owning row/parent reaches the
top/bottom 12% of the page AND its outer table edge, and a neighboring table at the opposite edge overlaps
at least 80% of normalized horizontal width. This is a continuation risk, not a
confirmed semantic link. Unreadable/missing evidence or unmatched source inventory
is unknown. Thresholds are generic. v1.0 replay is retained: it over-blocked Hana's
text-tight native cell and included an unrelated preceding row's page risk. v1.1
requires the outer table edge and accepts an exact span-set match when the physical
cell contains the native text-tight bbox. It does not widen coordinate tolerance.

Persist checked_no_gap/missing/unknown with source cell IDs, missing span IDs and
inspected pages. checked_no_gap means these bounded checks found no gap; distant
notes, unrecognized parents and non-aligned continuations can still be missed.

LangGraph: audit_source_context -> existing period decision subgraph ->
guard_absence_with_context. Keep raw generator and Judge outputs immutable. Guard
both independently, without comparing either against Gold. Only not_needed is
downgraded to insufficient_evidence when coverage is missing/unknown. Required
relations are preserved as candidates, but a sidecar with gaps is review_required;
they are never promoted to verified facts. Audit hashes bind checks to actual input.

Evaluation replays identical stored responses, with zero model calls. New wrapper
also accepts the existing bounded live adapter. No new model prompt, automatic
repair/retry, context expansion, new document schema, DB changes or fact publication.
Original reports and raw model errors stay intact. Before/after measures guard
behavior, not an improvement in model interpretation or newly recovered context.
