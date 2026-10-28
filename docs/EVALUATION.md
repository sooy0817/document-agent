# Evaluation scope

Snapshot: 2026-10-28. These numbers summarize locally recorded experiments. They are not a public, independently annotated benchmark.

## Separate outcomes

1. Extraction: did native text, regions, boundaries, cells, and merges survive?
2. Structure: are row/column parents and value paths consistent with the source?
3. Storage: does JSON survive a database round trip?
4. Retrieval: did the correct table and source cell appear?
5. Answer: did the answer use only eligible evidence and preserve citations/uncertainty?

A database write, a nonempty Tree, and a passing unit test each answer different questions.

## Retrieval comparison (development)

The comparison used 26 documents and 87 tables, with 47 development questions. Cell Exact Match was evaluated on the 37 answerable questions; the remaining 10 were not silently included as correct cell answers. Keyword+flat: 27/37; table-vector+flat: 14/37; vector+Tree: 32/37; hybrid+Tree: 32/37; keyword+Tree: 32/37.

This motivated a simple keyword+Tree demo path. It does not prove this is the best retrieval strategy for an unseen corpus. The original corpus and evaluation run are not included here, so these results are reported historical observations, not reproducible results of the bundled synthetic demo.

## Repeated-panel pilot

The narrow rule requires a complete unmerged grid, a uniquely repeating header sequence, independent text-bearing row labels, and at least two populated rows per panel. No institution names or expected rates participate.

Development: nine selected tables / 163 cells. The targeted Hana table improved from 3/5 to 5/5 joint value/path matches. A headerless table still had 0/8 known row mappings; its column meanings were unknown. New diagnostics expose this omission without inventing a repair.

After freezing 207 source/test/settings files, three previously unused domestic Korean PDF files were collected. Three selected inner tables / 66 cells were reviewed against source renderings and drawing coordinates:

- Hana: 16/16 cells and 6/6 joint value/path matches; same layout family as development.
- Woori: visible 4-column table became a 5-column grid with four extra empty cells. Joint matches remained 3/6. The rule correctly did not force a flat-panel repair onto this incomplete physical interpretation.
- KB: 34/34 cell bboxes and merged-slot shapes matched, but six headers contained duplicate overprinted text. Joint matches remained 0/10; the existing header/stub inference was wrong.

Hana/Woori evaluation covered diagram-free page 1 only; other pages were not counted as physically evaluated. KB's two pages were processed, but source-reference scoring covered one inner table on page 1. Source Gold was reviewed by the development assistant, not independently approved by a human reviewer. No rule was changed after these holdout sources were inspected.

Regression: HSBC and Gangdong NH re-extraction retained 373 physical cells, existing Trees/mappings and nested links. Two omission warnings were newly emitted for HSBC. All previously frozen 691 artifacts remained unchanged. This is non-regression evidence, not a new accuracy certification of every cell.

## Bundled verification

The included tests use synthetic fixtures and do not require the private corpus. Their current run results are recorded in `VERIFICATION.json` when available. The 97-test private-workspace count is not the count of this public subset. External connections are blocked during pytest and a dummy model key is injected.

New positive results from the same bank family are not sufficient evidence of cross-layout generalization. New institutions with held-out layout families, independent source annotation, and separate physical/semantic scoring remain necessary.
