# ADR-055 — Source-backed open-side, fragment, and shared-edge recovery

- Date: 2026-11-03
- Status: Implemented for new extraction runs; development evaluation only
- Contracts: PhysicalTableIR 2.1, StructuredTableIR and CanonicalDocument unchanged
- Algorithms: physical-grid/0.3.0, native-scoped/2

## Problem and decision

Three different physical failures preceded semantic interpretation in saved KB
review records. Do not fix them by bank name, expected grid size, semantic labels,
or a global point-tolerance increase. Existing files, review records and DB stay
unchanged; each execution writes a new output directory.

1. **Open outer sides:** admit a connected group with one interior vertical
   divider only when at least three horizontal positions have the same complete
   extent and the divider meets existing minimum coverage. Preserve original
   strokes. Existing endpoint inference marks outer boundaries as inferred.
2. **Separated header/body:** join a unique pair with identical vertical profiles
   (at least three positions), aligned left/right extents, a complete enclosing
   Native source candidate, a gap within the existing `max_small_gap`, and no
   intervening text. Do not fabricate bridging line segments. Competing pairings,
   different column profiles, large gaps, or absent source candidates abstain.
3. **Shared nested side:** an opposite inset plus source prose above/below can
   support a child sharing one vertical side. Preserve shared parent edges and
   reconstruct parent and child separately. Require source membership, restored
   parent cell, no unresolved child, unchanged span union and unrelated cells.
   Native padding fragments may map many-to-one only when they tile the physical
   cell horizontally without gaps/overlap and preserve per-cell nonempty spans.

Recovery issues and split sidecars preserve the rule/evidence used. Geometry
support is not semantic approval; no LLM, Judge, RAG, UI or financial fact changes.

## Actual verification

Artifacts: `reports/kb-physical-repair-20261103/review-01/comparison.html`.
Frozen source runs: `before-01`; isolated intermediate runs: `geometry-01`,
`shared-01`, `partition-01`; final: `after-01`. Intermediate failures are retained.

- KB p2: missing lower table -> 16 cells, 8 rows / 2 columns.
- KB p3: 3-cell header + 10-cell body -> one 13-cell table; its separate
  10-cell inner table retained. Both relevant parent links are linked.
- KB p4: mixed 20 cells -> 2 outer + 18 inner cells; nonrectangular parent
  resolved and inner parent link established.
- Source-reviewed physical partitions and merges: 47/47 across these targets.
  This is an analyst draft on development data, **not** approved independent Gold.
- Nonempty source spans retained: 16/16, 31/31, 25/25. Cell text character
  multisets also match; this is not a proof of reading order or semantics.
- Hana: existing 392 physical cells and 147 value-path candidates unchanged;
  one genuine open-sided 12-cell table additionally found on p3.
- Gangdong NH: existing 323 cells and 244 value-path candidates unchanged.
- KB full PDF freshly extracted: same physical geometry/text as frozen-IR
  replay on all 8 pages. Original 94 source/review files remain hash-identical.
- 117 offline tests: 116 pass, 1 existing sidecar test fails because
  `SNAPSHOT_MISMATCH` precedes expected `LINKAGE_ALGORITHM_MISMATCH`.
  Reproduced with the three pre-change modules loaded; not a new regression.
  Two pytest subtests also pass. Network is blocked and test keys are injected.

Commands executed include:

```powershell
.venv/Scripts/python.exe -X utf8 reports/kb-physical-repair-20261103/run_experiment.py before-01
.venv/Scripts/python.exe -X utf8 reports/kb-physical-repair-20261103/run_experiment.py after-01
.venv/Scripts/python.exe -m pytest tests/test_physical_grid.py tests/test_nested_regions.py tests/test_fill_boundaries.py tests/test_region_evidence.py tests/test_physical_recovery.py tests/test_semantic_generic.py tests/test_nested_content.py tests/test_nested_order.py tests/test_table_identity.py tests/test_pdf_integration.py tests/test_staging_layout.py -q --junitxml=reports/kb-physical-repair-20261103/tests.xml
.venv/Scripts/python.exe -X utf8 reports/kb-physical-repair-20261103/render_report.py
```

Run directories refuse overwrites; use a new name for another replay.

## Limits / pending work

- KB p2 incomplete Native fragments still compete with the restored Physical
  table. Parent linkage remains unresolved; physical success is reported apart
  from linkage success.
- Partial/local header semantics, p4 cross-page labels and p1 period hierarchy
  were not modified. Existing uncertainty stays intact.
- Fresh Korean holdout generalization is not established by this work.
- IDs may change in new runs after joining/splitting regions. Same unchanged
  cells are compared by geometry, source spans and text; a correspondence file
  is supplied. Old IDs/records are never rewritten or migrated silently.
- Complex ambiguous nesting, both-side-shared children, incomplete boundaries,
  or contradictory source partitions continue to abstain.
