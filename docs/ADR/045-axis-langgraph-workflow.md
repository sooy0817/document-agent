# ADR-045 — Conditional LangGraph workflow for axis candidates

Date: 2026-10-29. Status: implemented as opt-in sidecar; not default ingestion/RAG.

## Decision

Wrap existing rule_proposal, geometry_options, AxisModelAdapter, audit_region and
validated_views in a region-scoped LangGraph. Do not change their rules/prompt in
this experiment. The graph belongs to document preparation, before indexing,
not the question-answering graph. A graph is execution control, not a new judge.

inspect_source → judge_rules → assess_gap → [judge_llm if needed] →
validate_paths → materialize_candidate. At most one model invocation per region;
existing adapter imposes page/run call budget. Default is offline. Saved replay,
mock and explicit live runs are distinguished. No retry loops.

Supported rule candidates are protected from model replacement. Additions that
invalidate them are withheld as a batch, with a recorded reason. Invalid model
references are rejected by the existing validator. All proposal/audit output is
retained. Model failures preserve rules and reach final validation.

State records source digest, counts, rule/model proposals, routing, audit, errors,
usage record and derived StructuredTableIR. Existing JSON contracts stay intact.
Local output contains source text; no external callback/tracing export is enabled
by default. Source PDF, model keys and image bytes are not graph state fields.

## Accuracy experiment

Separate physical value isolation, rule/model candidate availability, accepted
path precision, overall correct coverage and rejected/unknown paths. Gold is
evaluation-only. Existing DB/iM documents are now regression data, not fresh
holdout. Saved-response replay isolates orchestration effects from model variance.

This change does NOT add merged/headerless region support, certify baseline
paths, reinterpret physically mixed cells, publish facts, or update Canonical/DB.
In particular, NO_GEOMETRY_OPTIONS is not a model failure or a correct abstention.
All target values stay in the overall denominator. Physical subset metrics must
be reported with their denominator and source-review limitations.

## References

LangGraph conditional edges: https://reference.langchain.com/python/langgraph/graph/state/StateGraph/add_conditional_edges
ADR-043/044 define the unchanged underlying resolver and checks.

## Executed regression

`reports/axis-graph-20261029/run-02/comparison.html` contains the actual runs.
Hana: 18 rule candidates; 21 with saved Text/Vision response replay. The known
three wrong continued-table proposals remain rejected. Gangdong StructuredTableIR
is unchanged. The prior DB/iM 35 targets retain 5 exact full paths; 19 values are
isolated/usable and 16 remain physically mixed. Correct coverage is 5/35 overall
and 5/19 on isolated values. No accuracy improvement is claimed. No new API calls.
Final targeted regression: 110 tests passed in 31.67 seconds; tests.xml and
verification.json record the command. All 33 local report links exist. Earlier
ADR-044 runtime freeze hashes remain unchanged.

Run-01 is retained; run-02 fixes report image references to original source crops
for replay directories that store only JSON. It does not change inference logic.

Reproduction (new output directory required):

```powershell
.venv/Scripts/python.exe scripts/run_axis_graph.py --source reports/axis-validation-20261029/run-01/final/hana-bounded --replay text --output reports/axis-graph-replay-new
.venv/Scripts/python.exe scripts/evaluate_axis_graph.py --previous reports/axis-validation-20261029/run-01 --output reports/axis-graph-evaluation-new
```

For a separately authorized live call use `--live text` or `--live vision` plus
`--allow-external --max-calls 1` in place of `--replay text`, with the API key in
the process environment. Usage/cost remains in model_record; old runs never mutate.
