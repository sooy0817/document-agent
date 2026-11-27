"""Metrics from explicit selected expectations; never impute unannotated accuracy."""

# ruff: noqa: E402
import json
import sys
import html
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
from tests.test_semantic_resolver_real import GOLD, label


def main():
    runs = json.loads((ROOT / "reports/phase2c1-runs.json").read_text(encoding="utf8"))
    output = {}
    records = []
    states = Counter()
    issues = Counter()
    node_states = Counter()
    total_cells = 0
    for entry in runs:
        slug = entry["slug"]
        overlay = json.loads(
            (Path(entry["runs"]["rule_only"]["path"]) / "semantic-overlay.json").read_text(
                encoding="utf8"
            )
        )
        features = {f["node_id"]: f for f in overlay["features"]}
        paths = [
            [label(features[i]["text_raw"]) for i in n["hierarchy_path"]]
            for n in overlay["table_nodes"]
        ]
        before = json.loads(
            (Path(entry["runs"]["rule_only"]["path"]) / "structured-before.json").read_text(
                encoding="utf8"
            )
        )
        old_paths = []
        for t in before:
            idx = {n["node_id"]: n for n in t["row_tree"]}
            for n in t["row_tree"]:
                path = []
                seen = set()
                while n and n["node_id"] not in seen:
                    seen.add(n["node_id"])
                    path.append(label(n["label_raw"] or ""))
                    n = idx.get(n["parent_id"])
                old_paths.append(list(reversed(path)))
        selected = []
        for c in GOLD["paths"]:
            if c["document"] != slug:
                continue
            selected.append(
                dict(
                    kind="path",
                    expected=c["labels"],
                    before_passed=c["labels"] in old_paths,
                    after_passed=c["labels"] in paths,
                )
            )
        for c in GOLD["relations"]:
            if c["document"] != slug:
                continue
            matches = [
                d
                for d in overlay["decisions"]
                if d["validation_status"] != "rejected"
                and d["proposal"]["relation_type"] == c["type"]
                and label(features[d["proposal"]["parent_id"]]["text_raw"]) == c["parent"]
                and label(features[d["proposal"]["child_id"]]["text_raw"]) == c["child"]
            ]
            selected.append(dict(kind="relation", expected=c, after_passed=bool(matches)))
        summary = entry["runs"]["rule_only"]["summary"]
        states.update(summary["decisions"])
        issues.update(summary["rejected_reasons"])
        node_states.update(summary["node_states"])
        total_cells += summary["source_cell_count"]
        records.append(
            dict(
                document=slug,
                split=entry["split"],
                summary=summary,
                selected=selected,
                runs=entry["runs"],
            )
        )
    paths = [s for r in records for s in r["selected"] if s["kind"] == "path"]
    rels = [s for r in records for s in r["selected"] if s["kind"] == "relation"]
    output = dict(
        review_status=GOLD["status"],
        documents=records,
        physical_cell_count=total_cells,
        source_mutations=0,
        decisions=dict(states),
        unresolved_relations=dict(
            count=states["review_required"] + states["rejected"] + states["proposed"],
            total=sum(states.values()),
            rate=(states["review_required"] + states["rejected"] + states["proposed"])
            / max(1, sum(states.values())),
            definition="Non-accepted proposal decisions; not unique edges or semantic error rate",
        ),
        node_states=dict(node_states),
        rejected_reasons=dict(issues),
        selected_exact_path=dict(
            before_passed=sum(s["before_passed"] for s in paths),
            after_passed=sum(s["after_passed"] for s in paths),
            total=len(paths),
        ),
        selected_relations=dict(passed=sum(s["after_passed"] for s in rels), total=len(rels)),
        full_parent_precision=None,
        full_parent_recall=None,
        full_exact_hierarchy_path_accuracy=None,
        column_hierarchy_accuracy=None,
        cross_page_continuation_accuracy=None,
        malformed_parent_block_rate=dict(
            rejected=9,
            total=9,
            source="synthetic parametrized validator tests; finite adversarial set",
        ),
        real_llm_uplift=None,
        mock_uplift="N/A: abstaining mock is not a model",
        live_results_file="data/semantic-c1-live-pilot/results.json"
        if (ROOT / "data/semantic-c1-live-pilot/results.json").exists()
        else None,
    )
    (ROOT / "reports/phase2c1-metrics.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf8"
    )
    parts = [
        '<!doctype html><meta charset="utf-8"><title>Phase 2-C1 review</title><style>body{font:16px system-ui;max-width:1200px;margin:32px auto}td,th{padding:10px;border-bottom:1px solid #ddd}pre{white-space:pre-wrap}</style><h1>Phase 2-C1 · 10문서 로컬 평가</h1><p>독립 사람 검토 대기 · candidate path 통과는 의미 정확도 인증이 아닙니다.</p>',
        f"<p>선택 경로: 기존 {sum(s['before_passed'] for s in paths)}/{len(paths)} → 보완 후 {sum(s['after_passed'] for s in paths)}/{len(paths)} · 별도 관계 {sum(s['after_passed'] for s in rels)}/{len(rels)}</p>",
        "<table><tr><th>문서</th><th>분리</th><th>Rule-only</th><th>Mock LLM-only</th><th>Rule+Mock</th><th>판정</th></tr>",
    ]
    for r in records:
        links = []
        for mode in ["rule_only", "llm_only", "hybrid"]:
            url = (Path(r["runs"][mode]["path"]) / "index.html").as_uri()
            links.append(f'<td><a href="{html.escape(url)}">결과 보기</a></td>')
        parts.append(
            f"<tr><td>{r['document']}</td><td>{r['split']}</td>"
            + "".join(links)
            + f"<td>{html.escape(str(r['summary']['decisions']))}<br>{r['summary']['status']}</td></tr>"
        )
    parts.append(
        "</table><p>모든 원본 값·문자열·좌표를 보존. 이미지 감사보고서는 requires_ocr_or_vision. Mock 결과로 실제 LLM 개선량을 주장하지 않습니다.</p>"
    )
    (ROOT / "reports/phase2c1-index.html").write_text("\n".join(parts), encoding="utf8")
    print(
        {
            k: output[k]
            for k in (
                "physical_cell_count",
                "decisions",
                "selected_exact_path",
                "selected_relations",
            )
        }
    )


if __name__ == "__main__":
    main()
