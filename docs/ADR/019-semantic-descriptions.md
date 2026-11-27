# ADR-019 — Source-grounded SemanticDescription sidecar

Status: additive local implementation. No frozen IR/Overlay/Canonical fields change.

A SemanticDescriptionBundle references exact EvidenceBundle and StructureProposal
hashes. Separate table descriptions, row/metric definitions and analytical hypotheses.
Assertions carry typed relation endpoints and evidence references; matching them to
proposal edges is deterministic. Free prose correctness is explicitly NOT established
by this check: it remains subject to human review. Analytical interpretations remain
hypotheses even after editorial approval. Missing definitions/formulas are unresolved.

Description revisions and structure revisions are saved together in the existing
review ledger mechanics via a separate store adapter and output directory. Every
change invalidates approvals for that revision; original snapshots are retained.
Candidate/final-Gold/publication-record gates reuse the existing separate gates.
A description-scoped approval does not certify the entire table. Local names are
self-declared; production authentication remains out of scope.

This turn's real-PDF interpretation is interactive Codex source analysis, not an
externally invoked model or replayable automated inference endpoint. Model-assisted
comparison remains not_executed. Source extraction scripts are reproducible; curated
interpretations are recorded observations, not parser rules or independently reviewed
Gold. Namwon remains development, Nampyeong untouched. New institutions are purposive
diagnostic samples, not a generalization estimate.

Reuse needs a context signature (institution, document hash, period, unit, formula,
header meaning); identical labels never imply identical definitions. No automatic
reuse or production RAG/DB publication. Search previews remain provisional.
