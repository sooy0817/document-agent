# ADR-044: Source coverage, role conflict and header evidence gates

2026-10-29. Candidate validation only; no semantic certification or fact publication.

Add `axis_validation.audit_region` after rules/model proposals and before materializing
StructuredTableIR candidate views. Physical/Structured/Canonical contracts stay intact.
The original geometry validator and previous artifacts remain unchanged. The opt-in
runner records geometry results separately from the stricter audit.

Every source cell gets an audit entry with exact text and span IDs, including blanks,
malformed cells, explicit abstentions and omitted content. Coverage is not mapping
accuracy. Referenced numeric row keys without value mappings are flagged rather than
being silently accepted as labels. Contradictory value/row/column roles invalidate
all proposed links involving the conflicting cell.

Header evidence is recomputed independently of model selections/confidence. Existing
numeric matrix and source appearance rules are reused. A conservative compact-band
hypothesis additionally requires at least two short distinct nonnumeric labels,
two aligned body rows with textual stubs, and a right-body median length at least
twice the maximum header label length. Compact labels are <=12 nonspace characters;
headers supported by other rules are <=40 and not sentence-ending prose. This is a
document-independent heuristic, not proof of header semantics. Unsupported/continued
and absent-header tables remain unresolved. No document names, hashes, page numbers,
expected values or evaluation Gold participate in these decisions.

Old model proposals are replayed to isolate validator behavior without new API calls.
Freeze implementation before acquiring/inspecting a new PDF. Report false acceptance,
false rejection, retained sources, unresolved cells and old-tree preservation separately.
No baseline mappings are silently promoted; existing malformed physical cells and
missing continuation/absent-axis support are outside this change.

The new candidate revision explicitly clears paths (not values) for rejected targets
and baseline mappings referencing a rejected header. Baseline files and diagnostic
nodes remain intact; unrelated baseline mappings are not implicitly re-certified.
`audit_mapping_coverage` also inspects existing IR, independently of whether any new
proposal exists, to expose numeric row identifiers lacking a value mapping.

Results: reports/axis-validation-20261029/run-01/comparison.html. Saved development
responses: 3 of 3 continued-table false links withheld, all 21 Hana text/vision
candidates retained, 9 numeric row values missing from old mappings diagnosed.
Gangdong raw tree and mappings unchanged. After runtime freeze, two unseen Korean
PDFs were source-reviewed before extraction. The stress probe submitted all available
geometry candidates (not new LLM responses): 1 wrong header relation withheld and
7 source-supported relations retained. Of 35 predeclared value targets, 19 existed as
isolated physical cells, 16 remained mixed into outer text, and only 5 had matching
baseline paths. New rules proposed no paths on these PDFs. Candidate absence and
physical/semantic extraction failure remain in denominators. 90 tests passed; no
new model calls. This is a validator pilot, not general semantic recovery success.
