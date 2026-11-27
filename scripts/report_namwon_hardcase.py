"""Offline analysis of live hard-case responses against frozen provisional targets."""

# ruff: noqa: E402
import hashlib
import html
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from doc_understanding.agents.semantic_resolver.engine import digest
from doc_understanding.agents.semantic_resolver.models import SemanticOverlay
from doc_understanding.domain.models import DocumentIR
from doc_understanding.parser.physical.physical_models import PhysicalTableIR
from doc_understanding.parser.structure.models import StructuredTableIR

BASE = ROOT / "data/namwon-holdout"
PLAN = BASE / "pilot-plan"
OUT = BASE / "live-hardcase"


def read(p):
    return json.loads(p.read_text(encoding="utf8"))


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    pre = read(PLAN / "preflight.json")
    gold = read(BASE / "golden-review.json")
    results = read(OUT / "results.json")
    source = SemanticOverlay.model_validate_json(Path(pre["baseline_overlay_path"]).read_bytes())
    fs = {f.node_id: f for f in source.features}
    selected = [c for c in gold["cases"] if c["id"] in pre["evaluated_case_ids"]]
    baseline = {n.semantic_node_id: n.model_dump(mode="json") for n in source.table_nodes}
    prior = {(c.parent_id, c.child_id, c.relation_type) for c in source.candidates}
    details = []
    rows = []
    call_rows = []
    extra = []
    totals = []
    for result in results:
        req = read(PLAN / (result["request_id"] + ".json"))
        page = req["nodes"][0]["page_number"]
        ids = set(req["allowed_node_ids"])
        mode = result["mode"]
        nodes = {n["semantic_node_id"]: n for n in result["nodes"]}
        call_rows.append(result["call"])
        raw = result["proposal"]["patches"]
        rawkeys = {(p["parent_id"], p["child_id"], p["relation_type"]) for p in raw}
        abstain = (
            set(result["proposal"]["abstain_node_ids"])
            if result["call"]["status"] == "completed"
            else set()
        )
        for p in raw:
            membership = (
                p["parent_id"] in ids
                and p["child_id"] in ids
                and set(p["evidence_ids"]) <= set(req["allowed_evidence_ids"])
            )
            extra.append(
                dict(
                    request_id=result["request_id"],
                    parent_id=p["parent_id"],
                    child_id=p["child_id"],
                    parent=fs[p["parent_id"]].text_raw if p["parent_id"] in fs else p["parent_id"],
                    child=fs[p["child_id"]].text_raw if p["child_id"] in fs else p["child_id"],
                    relation=p["relation_type"],
                    membership_valid=membership,
                    origin="existing_rule_candidate"
                    if (p["parent_id"], p["child_id"], p["relation_type"]) in prior
                    else "new_edge_proposal",
                    reason=p["reason"],
                )
            )
        cases = [
            c
            for c in selected
            if c["parent_anchor"]["node_id"] in ids and c["child_anchor"]["node_id"] in ids
        ]
        for c in cases:
            child = c["child_anchor"]["node_id"]
            parent = c["parent_anchor"]["node_id"]
            n = nodes[child]
            actual = n["semantic_parent_id"]
            correct = actual == parent and n["relation_type"] == "parent_child"
            rp = [p for p in raw if p["child_id"] == child and p["relation_type"] == "parent_child"]
            rec = dict(
                case_id=c["id"],
                page=page,
                mode=mode,
                baseline_status=c["status"],
                expected_parent=fs[parent].text_raw,
                child=fs[child].text_raw,
                predicted_parent=fs[actual].text_raw if actual in fs else actual,
                predicted_relation=n["relation_type"],
                wrong_parent=bool(actual) and actual != parent,
                wrong_relation=actual == parent and n["relation_type"] != "parent_child",
                correct=correct,
                raw_correct=(parent, child, "parent_child") in rawkeys,
                expected_edge_in_rule_candidates=(parent, child, "parent_child") in prior,
                wrong=bool(actual) and not correct,
                missing=not actual,
                explicit_abstain=child in abstain,
                silent_omission=result["call"]["status"] == "completed"
                and not any(p["child_id"] == child for p in raw)
                and child not in abstain,
                missing_parent_child_proposal=not rp,
                call_completed=result["call"]["status"] == "completed",
                validation_status=n["validation_status"],
                candidate_parent_ids=n["candidate_parent_ids"],
            )
            details.append(rec)
        # Provisional source-reviewed constraints, outside the 14-edge score.
        for c in gold["cases"]:
            if c["id"] not in (
                "negative-total-inventory",
                "negative-loan-total",
                "negative-deposit-total",
            ):
                continue
            child = c["child_anchor"]["node_id"]
            if child not in nodes:
                continue
            n = nodes[child]
            b = baseline[child]
            totals.append(
                dict(
                    page=page,
                    mode=mode,
                    child_id=child,
                    baseline_parent=fs[b["semantic_parent_id"]].text_raw
                    if b["semantic_parent_id"]
                    else None,
                    output_parent=fs[n["semantic_parent_id"]].text_raw
                    if n["semantic_parent_id"] in fs
                    else n["semantic_parent_id"],
                    over_hierarchy=bool(n["semantic_parent_id"]),
                    explicit_abstain=child in abstain,
                )
            )
    for mode in ("llm_only", "hybrid"):
        ds = [d for d in details if d["mode"] == mode]
        correct = sum(d["correct"] for d in ds)
        pred = sum(not d["missing"] for d in ds)
        rows.append(
            dict(
                mode=mode,
                evaluated=len(ds),
                correct=correct,
                predicted=pred,
                precision=correct / pred if pred else None,
                recall=correct / len(ds) if ds else None,
                raw_correct=sum(d["raw_correct"] for d in ds),
                recovered_missing=sum(
                    d["correct"] and d["baseline_status"] == "missing_parent" for d in ds
                ),
                corrected_wrong=sum(
                    d["correct"] and d["baseline_status"] == "wrong_parent_or_relation" for d in ds
                ),
                damaged_correct=sum(
                    not d["correct"] and d["baseline_status"] == "correct" for d in ds
                ),
                new_wrong=sum(
                    d["wrong"] and d["baseline_status"] in ("correct", "missing_parent") for d in ds
                ),
                wrong=sum(d["wrong"] for d in ds),
                wrong_parent=sum(d["wrong_parent"] for d in ds),
                wrong_relation=sum(d["wrong_relation"] for d in ds),
                missing=sum(d["missing"] for d in ds),
                explicit_abstain=sum(d["explicit_abstain"] for d in ds),
                silent_omission=sum(d["silent_omission"] for d in ds),
                new_correct_edges=sum(
                    d["raw_correct"] and not d["expected_edge_in_rule_candidates"] for d in ds
                ),
                chosen_existing_correct_edges=sum(
                    d["raw_correct"] and d["expected_edge_in_rule_candidates"] for d in ds
                ),
                net_delta=correct - sum(d["baseline_status"] == "correct" for d in ds),
                selected_p9_exact_path_correct=all(d["correct"] for d in ds if d["page"] == 9),
            )
        )
    for path, expected in read(BASE / "frozen-baseline.json")["files"].items():
        assert sha(ROOT / path) == expected
    assert sha(Path(pre["baseline_overlay_path"])) == pre["baseline_overlay_sha256"]
    assert sha(BASE / "source-annotations.json") == pre["golden_sha256"]
    run = read(BASE / "runs.json")[0]
    pr = Path(run["physical_run"])
    doc = DocumentIR.model_validate_json((pr / "document-ir.json").read_bytes())
    assert digest(doc) == source.input_hashes["document"]
    cells = 0
    for p in (pr / "physical").glob("page-*-table-*.json"):
        t = PhysicalTableIR.model_validate_json(p.read_bytes())
        assert digest(t) == source.input_hashes[f"physical:{t.table_id}"]
        cells += len(t.cells)
    for data in read(Path(run["semantic_run"]) / "structured-before.json"):
        t = StructuredTableIR.model_validate(data)
        assert digest(t) == source.input_hashes[f"structured:{t.table_id}"]
    registry = read(BASE / "registry.json")[0]
    assert sha(Path(registry["path"])) == registry["sha256"]
    report = dict(
        status="provisional_independent_human_review_pending",
        actual_calls=sum(c["attempts"] for c in call_rows),
        completed=sum(c["status"] == "completed" for c in call_rows),
        input_tokens=sum(c["input_tokens"] or 0 for c in call_rows),
        output_tokens=sum(c["output_tokens"] or 0 for c in call_rows),
        estimated_usd=sum(c["estimated_cost_usd"] or 0 for c in call_rows),
        unknown_cost_calls=sum(c["estimated_cost_usd"] is None for c in call_rows),
        call_seconds=sum(c["elapsed_seconds"] for c in call_rows),
        calls=call_rows,
        baseline_comparison=dict(
            mode="rule_only",
            evaluated=14,
            correct=6,
            predicted=9,
            precision=6 / 9,
            recall=6 / 14,
            missing=5,
            wrong_parent=3,
            selected_p9_exact_path_correct=True,
        ),
        comparison=rows,
        case_results=details,
        proposal_audit=extra,
        total_scope_checks=totals,
        validation_counts=dict(
            Counter(d["validation_status"] for r in results for d in r["decisions"])
        ),
        validation_reasons=dict(
            Counter(reason for r in results for d in r["decisions"] for reason in d["reasons"])
        ),
        source_unchanged=True,
        physical_cells_verified=cells,
        all_contracts_unchanged=True,
        mock_calls=0,
    )
    assert report["actual_calls"] == 8 and report["completed"] == 8
    assert report["estimated_usd"] <= 0.25
    assert all(r["evaluated"] == 14 for r in rows)
    (OUT / "comparison.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8"
    )
    parts = [
        '<!doctype html><meta charset="utf-8"><title>남원 실제 LLM 비교</title><style>body{font:16px system-ui;max-width:1250px;margin:30px auto}td,th{padding:8px;border:1px solid #ccd}table{border-collapse:collapse}pre{white-space:pre-wrap}</style><h1>남원 Hard-case · 실제 LLM 비교</h1><p>독립 사람 검토 전 provisional · 14개 선택 부모 관계 · 원본/기존 IR 불변</p><p><a href="comparison.json">전체 비교 JSON</a> · <a href="results.json">실제 Patch/usage</a> · <a href="../index.html">원본 골든 검토</a></p>',
        "<p>Rule-only 6/14 · LLM-only 2/14 · Hybrid 8/14. 실제 8회, 추정 $0.0617948. 전체 26개 중 나머지 12개는 N/A.</p>",
        "<pre>" + html.escape(json.dumps(rows, ensure_ascii=False, indent=2)) + "</pre>",
        "<table><tr><th>사례</th><th>방식</th><th>기존</th><th>기대 부모</th><th>실제 부모</th><th>관계 유형</th><th>정답</th><th>기존 Rule 후보 존재</th></tr>",
    ]
    for d in details:
        parts.append(
            "<tr>"
            + "".join(
                "<td>" + html.escape(str(d[k])) + "</td>"
                for k in (
                    "case_id",
                    "mode",
                    "baseline_status",
                    "expected_parent",
                    "predicted_parent",
                    "predicted_relation",
                    "correct",
                    "expected_edge_in_rule_candidates",
                )
            )
            + "</tr>"
        )
    parts.append(
        "</table><h2>총계 범위 보조 검사</h2><pre>"
        + html.escape(json.dumps(totals, ensure_ascii=False, indent=2))
        + "</pre>"
    )
    (OUT / "index.html").write_text("\n".join(parts), encoding="utf8")
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "actual_calls",
                    "completed",
                    "input_tokens",
                    "output_tokens",
                    "estimated_usd",
                    "call_seconds",
                    "comparison",
                    "validation_counts",
                    "validation_reasons",
                    "total_scope_checks",
                )
            },
            ensure_ascii=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
