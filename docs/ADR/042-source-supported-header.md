# ADR-042 — Source-supported flat header hypothesis

2026-10-29. Implemented as semantic-geometry/0.3.0; evaluation recorded separately.

## Problem
The 30-question frozen pilot found six questions whose candidate cell paths were
usable geometrically but withheld by HEADER_REGION_AMBIGUOUS. Top-left merging
alone cannot distinguish an unmerged header row from a first data row.

## Decision
Accept an optional original PageIR alongside PhysicalTableIR. Preserve both IRs.
For a complete, non-overlapping, rectangular panel with one header row and one
stub column, require all of:

* all cells nonblank and no unresolved physical cell;
* a complete singleton header row, distinct compact labels containing letters;
* at least two body value cells, either all containing numeric tokens or a
  majority (at least two) substantially longer than the header labels;
* source span identity, document/page, positive area, >=85% cell containment,
  and character coverage of the header labels (PDF object order is not reading order);
* either every header cell has a rectangular fill covering >=85% of its area
  with fill area <=1.5 times the cell, and no value cell has such fill; or all
  header spans are explicitly bold and no body-value span is bold.

Fill presence is observed from drawing commands, not guessed colour. A rectangle
may be a PDF `re` or four axis-aligned edges forming its bbox. Rules contain no
document names, years, expected answers or per-PDF row/column counts. Thresholds
define the bounded hypothesis; they are not calibrated correctness probabilities.

Only the header ambiguity is disambiguated. LAYOUT_HYPOTHESIS, inferred states,
independent_human_review=pending, path collisions and all other guards remain.
SOURCE_HEADER_SUPPORTED is an informational Issue retaining cell/span/drawing
source references and the rule's positive evidence. No schema fields are added.
The analyzer_version enum gains 0.3.0; old 0.1/0.2 artifacts remain readable.
Physical-only calls have no new evidence and retain the old conservative behavior.
The normal PDF staging path passes the original page; Canonical fallback also does.

## Deliberate limits
No unstyled label-only interpretation, multi-row header inference, repeated-panel
repair, numerical-first-row promotion, fill-colour reconstruction, global hold
override, LLM, or RAG/search/prompt modification. Even style plus type contrast
can be a false positive in some PDFs; this remains a source-supported hypothesis,
not an independent semantic fact. Evidence absent => preserve the hold.

## Evaluation protocol
Existing 30 questions retain their prewritten source references and corpus.
Physical/Document IR are reused byte-for-byte in new immutable run directories
to isolate the semantic change. New Korean PDF(s) are selected/inspected only
after the implementation hash is frozen. No tuning after holdout inspection.
Report structure-level resolution separately from retrieval, complete evidence,
and final answer success. Original pilot and output artifacts remain unchanged.
