# ADR-032 — Opt-in thin filled rectangle boundary experiment

2026-10-23. Experimental, not the production default. PhysicalTableIR 2.1,
StructuredTableIR 2.0 and CanonicalDocument schemas are unchanged.

2026-10-27 integration note: the original experiment and its artifacts remain
frozen. Following the financial/property comparison, the user authorized F0
as the default at `eval.physical.evaluate_pdf` / `staging.prepare`, recorded as
`boundary_profile="stroke_fill"`. The low-level reconstructor still defaults
to stroke; `boundary_profile="stroke"` retains the prior orchestration input.
No S1, raster policy, or nested-region change accompanies this promotion.
See `reports/f0-default-integration-20261027/comparison.html` for verification
and the separately proposed nested-region plan. The text below describes the
original opt-in experiment, not the updated orchestration default.

## Evidence and minimum change

PyMuPDFExtractor already retains drawing commands and drawing_stroked=False
for fill-only PDF paths. LineDetector.vector skips every such block. The
evaluate_pdf raster preparation predicate independently treats any drawing as
sufficient to omit pixels. Thus native table region hints can survive while
PhysicalGridReconstructor receives no grid evidence.

PhysicalGridReconstructor now accepts an optional detector dependency. Its
default detector, line selection, region detection, cell resolver, text
assignment and all downstream semantics remain unchanged. The opt-in
physical_experiment.fill_boundaries.FillBoundaryDetector retains existing
stroke candidates and adds one centerline for an axis-aligned fill-only `re`
command if thickness is >0 and <=2*tolerance and length >=max(2*tolerance,
6*thickness). Default tolerance is 0.75 PDF pt. Short real fragments are kept,
not stretched. Existing coverage/gap checks determine connected boundaries.
Large shading rectangles, squares, curves and arbitrary polygon fills are
excluded. No filename, page, text, expected grid size or Gold is accessed.

The existing method=vector/evidence_kind=fill_transition fields carry these
conservative inferred candidates. `/fill-center/<command-index>` and the
original SourceRef retain exact command provenance. This reuses an existing
category rather than calling a filled rectangle an observed stroke. It is a
centerline hypothesis, not a raster transition measurement. The separate
run log states the interpretation. Source rectangle coordinates stay in IR.

## Evaluation and freeze

The completed 8-table pilot remains immutable (203 artifact hashes and a
baseline-code archive). W3C/Texas are development, NH/PNEUMA regression and
TUTA a partial-grid diagnostic. F0 adds fill to S0; F1 adds fill to the existing
S1 short-fragment policy. S2/S3 are not used. Gold is read only after runs.
Use the pilot's unchanged offline comparisons, not a new scoring/routing
component. Require geometry + span + occupied_slots + text + no false edges
for a full physical-table match. Semantic abstentions remain separate.

Freeze code, tests, runner and comparison code before acquiring/inspecting
new holdout PDFs; do not tune after holdout. Record all selected cases.

## Limits

Drawing fill color, opacity, clipping and occlusion are not represented in
the current DocumentIR. Geometry alone cannot guarantee a visible boundary;
thin decoration and white fills can become false candidates. Thick borders,
rotated/quadrilateral fills and image-only rules are outside this experiment.
No semantic validation, RAG, LLM/VLM, evaluator agent, UI, network service or
database change is introduced. Report results and stop after this experiment.
