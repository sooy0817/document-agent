"""Numerical precheck of already-proposed scopes. Never generates hierarchy or Gold."""

import hashlib
import json
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
from collections import Counter
from doc_understanding.eval.aggregation_arithmetic import parse_amount


def recompute(root: Path):
    legacy_path = root / "data/tree-phase3a-triage/arithmetic.json"
    legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
    cells = {}
    hashes = {}
    for name, expected in legacy["source_sha256"].items():
        p = root / name
        actual = hashlib.sha256(p.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError("기존 검산 근거가 변경됨: " + name)
        hashes[name] = actual
        if name.endswith("-physical.json"):
            raw = json.loads(p.read_text(encoding="utf-8"))
            cells.update({c["cell_id"]: c for c in raw["cells"]})
    zero_ids = set()
    for c in legacy["checks"]:
        zero_ids.update(c["blank_zero_cell_ids"] + c["dash_zero_cell_ids"])

    def evaluate(members, total):
        def amount(x):
            return parse_amount(
                x["text"],
                dash_as_zero=x["cell_id"] in zero_ids,
                blank_as_zero=x["cell_id"] in zero_ids,
            )

        values = [amount(x) for x in members]
        t = amount(total)
        if not values or any(x is None for x in values) or t is None:
            return dict(status="unresolved", sum=None, difference=None)
        s = sum(values, Decimal(0))
        return dict(status="match" if s == t else "mismatch", sum=str(s), difference=str(s - t))

    results = []
    for i, c in enumerate(legacy["checks"]):
        for source in c["members"] + [c["reported_total"]]:
            if (
                source["cell_id"] not in cells
                or cells[source["cell_id"]]["original_text"] != source["text"]
            ):
                raise ValueError("원본 값 불일치")
        case = next(
            (
                k.parent.name
                for k in (root / "reports/architecture-v1-contract-draft/cases").glob(
                    "*/evidence.json"
                )
                if c["reported_total"]["cell_id"] in k.read_text(encoding="utf-8")
            ),
            None,
        )
        results.append(
            dict(
                check_id=f"prior-{i}",
                case_id=case,
                page=c["page"],
                label=c["label"],
                column_index=c["column_index"],
                members=c["members"],
                reported_total=c["reported_total"],
                scope_origin="existing_provisional_annotation",
                source_values_verified=True,
                **evaluate(c["members"], c["reported_total"]),
            )
        )
    # A separate numerical hypothesis, never an adopted rounding policy or hierarchy rule.
    for check in results:
        if check["status"] != "mismatch":
            continue
        operands = []
        for member in check["members"]:
            cell = cells[member["cell_id"]]
            prefix = member["cell_id"].rsplit("/cell", 1)[0]
            row = [
                c
                for cid, c in cells.items()
                if cid.startswith(prefix + "/cell") and c["row_index"] == cell["row_index"]
            ]
            base = next((c for c in row if c["column_index"] == cell["column_index"] - 1), None)
            rate = next((c for c in row if c["column_index"] == cell["column_index"] + 1), None)
            if not base or not rate or not rate["original_text"].strip().endswith("%"):
                break
            b = parse_amount(base["original_text"])
            r = parse_amount(rate["original_text"].strip()[:-1])
            observed = parse_amount(member["text"])
            if b is None or r is None or observed is None:
                break
            product = b * r / Decimal(100)
            if product.quantize(Decimal("1"), rounding=ROUND_HALF_UP) != observed:
                break
            operands.append(
                dict(
                    base_cell_id=base["cell_id"],
                    base_raw=base["original_text"],
                    rate_cell_id=rate["cell_id"],
                    rate_raw=rate["original_text"],
                    product=str(product),
                )
            )
        if len(operands) == len(check["members"]) and operands:
            unrounded = sum((Decimal(o["product"]) for o in operands), Decimal(0))
            rounded = unrounded.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            if rounded == parse_amount(check["reported_total"]["text"]):
                check["rounding_hypothesis"] = dict(
                    operands=operands,
                    unrounded_sum=str(unrounded),
                    rounded_sum=str(rounded),
                    rounding="ROUND_HALF_UP to integer",
                    status="numerically_consistent_hypothesis_not_confirmed_author_policy",
                )
    # Current contract relation -> referenced rows -> matching physical columns.
    current = []
    for path in (root / "reports/architecture-v1-contract-draft/cases").glob("*/canonical.json"):
        can = json.loads(path.read_text(encoding="utf-8"))
        p = can["proposal"]
        e = can["evidence"]
        byid = {x["evidence_id"]: x for x in e["items"]}
        nodes = {x["node_id"]: x for x in p["nodes"]}
        grid = {}
        for x in e["items"]:
            if x["kind"] == "cell":
                grid.setdefault((x["row_index"], x["column_index"]), []).append(x)
        for rel in p["relations"]:
            if rel["type"] != "AGGREGATES":
                continue
            op = next(
                (o for o in p["operations"] if o["operation_id"] == rel["operation_id"]), None
            )
            if (
                not op
                or op["operator"] != "sum"
                or any(o["coefficient"] != 1 for o in op["operands"])
            ):
                continue
            ids = [rel["source"]] + rel["targets"]
            anchors = []
            for nid in ids:
                node = nodes.get(nid)
                anchor = (
                    byid.get(node["anchors"][0]["evidence_id"])
                    if node and len(node["anchors"]) == 1
                    else None
                )
                if not anchor or anchor["kind"] != "cell" or anchor["rowspan"] != 1:
                    break
                anchors.append(anchor)
            if len(anchors) != len(ids):
                current.append(
                    dict(
                        check_id=path.parent.name + "-" + rel["claim_id"],
                        case_id=path.parent.name,
                        status="unresolved",
                        reason="집계 노드와 단일 물리 행의 연결이 불충분",
                        scope_origin="contract_design_proposal",
                    )
                )
                continue
            for col in sorted({x["column_index"] for x in e["items"] if x["kind"] == "cell"}):
                if col in {a["column_index"] for a in anchors}:
                    continue
                slot = [grid.get((a["row_index"], col), []) for a in anchors]
                if any(len(s) != 1 or s[0]["colspan"] != 1 for s in slot):
                    current.append(
                        dict(
                            check_id=f"{path.parent.name}-{rel['claim_id']}-{col}",
                            case_id=path.parent.name,
                            status="unresolved",
                            reason="병합 또는 누락 값 셀",
                            scope_origin="contract_design_proposal",
                        )
                    )
                    continue
                bound = [dict(text=s[0]["text_raw"], cell_id=s[0]["source_id"]) for s in slot]
                current.append(
                    dict(
                        check_id=f"{path.parent.name}-{rel['claim_id']}-{col}",
                        case_id=path.parent.name,
                        page=anchors[0]["page"],
                        label=anchors[0]["text_raw"],
                        column_index=col,
                        scope_origin="contract_design_proposal",
                        claim_id=rel["claim_id"],
                        members=bound[1:],
                        reported_total=bound[0],
                        **evaluate(bound[1:], bound[0]),
                    )
                )
    return dict(
        schema_version="human-precheck/0.1",
        legacy_checks=results,
        legacy_counts=dict(Counter(c["status"] for c in results)),
        proposal_checks=current,
        proposal_counts=dict(Counter(c["status"] for c in current)),
        source_sha256=hashes,
        scope_not_inferred_from_numbers=True,
        semantic_truth="not_established",
        gold_approval=False,
        fact_published=False,
        external_calls=0,
        zero_policy="Previously user-approved bound cell IDs only; missing sources never zero",
    )


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[3]
    report = recompute(root)
    out = root / "data/human-review/precheck.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report["legacy_counts"], report["proposal_counts"])
