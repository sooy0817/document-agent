# ADR-053: Source-grounded continuation review graph

Date: 2026-11-03. Opt-in experiment; no production semantic approval.

The existing conservative logical-table rule remains unchanged. A separate
LangGraph discovers adjacent-page edge fragments and vertically consecutive
same-page fragments without requiring identical column counts or row labels.
Candidate discovery is not a detector for physically missing tables, nested
continuations, diagrams or all multi-column layouts.

State retains source packet, rules-only comparator, proposed relation, validation,
review and final disposition. `Annotated[list, operator.add]` accumulates only new
step/error/model records. Source artifacts never enter external tracing callbacks.

Nodes: collect_evidence -> assess_coverage -> judge_relation -> validate_relation
-> review_relation -> finalize. An insufficient judgment may expand source pages
once, then return to coverage/judgment. Maximum two judgments and one review per
pair; no repair loop. Physical failures and input budgets hold without inference.

The model uses original images plus original cells/spans; rule proposals and
reference labels are withheld. Table continuity, column header inheritance and
row-group continuation are separate. Target IDs define application scope. Local
target labels must not be overwritten by previous-page group labels. Exact quotes,
membership, predecessor ordering, competing scope assignments and physical status
are deterministic gates, not proofs of semantic truth. A separate source-based
review can reject or abstain. Agreement still means candidate, not approved fact.

External image inference is explicit CLI opt-in using the existing GPT-4.1 transport.
Usage and estimated cost remain in immutable per-call records, including failures.
Langfuse uses one custom root and child spans; no parallel callback tree. Raw PDF,
image bytes, API keys and full source packets are excluded from telemetry. Local
review reports contain original source/IDs and evidence, never credentials.

CanonicalDocument, PhysicalTableIR, StructuredTableIR, original extraction runs,
operating DB and RAG publication remain unchanged. Results are review sidecars.
Prepared cases/code/reference hashes are frozen before live inference. The LH
pilot has development references, not independent human Gold or universal scores.

The report must distinguish pair relationship agreement, selected scope checks,
invalid proposals, review rejection, abstention, wrong links, model cost and latency.
Non-link from baseline rules is not an explicit negative judgment.
