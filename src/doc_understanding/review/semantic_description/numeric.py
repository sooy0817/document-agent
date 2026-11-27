"""Exact arithmetic on proposed row operations and bound matching column paths."""

from decimal import Decimal
from doc_understanding.validation.arithmetic import parse_amount


def numeric_checks(evidence, proposal):
    ev = {x.evidence_id: x for x in evidence.items}
    bindings = {}
    for b in proposal.bindings:
        if b.row_path and b.column_path:
            bindings.setdefault((b.row_path[-1], b.column_path), []).append(b)
    column_nodes = {n.node_id for n in proposal.nodes if n.axis == "column"}
    rows = []
    for op in proposal.operations:
        if op.operator != "sum":
            continue
        for (node, path), bs in bindings.items():
            if node != op.result_ref:
                continue
            # Existing relation scope can constrain column nodes. Row scopes remain unchanged.
            scopes = [r.scope for r in proposal.relations if r.operation_id == op.operation_id]
            if any(
                (set(scope.excluded) & column_nodes & set(path))
                or (
                    set(scope.included) & column_nodes
                    and not (set(scope.included) & column_nodes & set(path))
                )
                for scope in scopes
            ):
                continue
            selected = [bindings.get((o.ref, path), []) for o in op.operands] + [bs]
            ids = [b[0].cell_evidence_id for b in selected if len(b) == 1]
            values = [parse_amount(ev[x].text_raw) for x in ids]
            if len(ids) != len(selected) or any(v is None for v in values):
                rows.append(
                    dict(
                        operation_id=op.operation_id,
                        column_path=path,
                        status="unresolved",
                        evidence_ids=ids,
                        reason="blank, nonstandard sign or missing/ambiguous binding; no zero assumption",
                    )
                )
                continue
            calculated = sum(
                (v * o.coefficient for v, o in zip(values[:-1], op.operands)), Decimal(0)
            )
            rows.append(
                dict(
                    operation_id=op.operation_id,
                    column_path=path,
                    status="match" if calculated == values[-1] else "mismatch",
                    evidence_ids=ids,
                    calculated=str(calculated),
                    reported=str(values[-1]),
                    difference=str(calculated - values[-1]),
                    semantic_approval=False,
                )
            )
    return rows
