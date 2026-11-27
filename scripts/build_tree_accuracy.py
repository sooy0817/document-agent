"""Namwon-only annotation fixture + generic evaluator adapter. No model calls."""

import json
from collections import Counter
from html import escape
from pathlib import Path

import fitz

from doc_understanding.evaluation.parser.tree_accuracy import (
    box,
    digest,
    evaluate_table,
    iou,
    micro,
    norm,
    path,
    read,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/tree-phase3a"


def save(name, value):
    OUT.mkdir(exist_ok=True, parents=True)
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def specs():
    # Manually read original 1.5x page images. Coordinates are image pixels, not model grids.
    # Rows and grouping authored from visible originals, not predicted hierarchy.
    return [
        dict(
            page=42,
            index=0,
            title="재고자산·생물자산",
            x=[108, 237, 367, 497, 626, 755],
            y=[395, 424, 453, 482, 511, 541, 570, 599, 628, 657, 687, 716, 745, 775, 804],
            labels=[
                "구분",
                "재고자산",
                "사료",
                "축산자재",
                "마트상품",
                "판매품",
                "제품",
                "가공재로",
                "저장품",
                "기타재고",
                "재고자산소계",
                "생물자산",
                "생물자산소계",
                "합계",
            ],
            groups={1: list(range(2, 11)), 11: [12]},
            merges=[],
            header=1,
            agg={10: list(range(2, 10)), 13: [10, 12]},
            unknown_agg=[12],
        ),
        dict(
            page=43,
            index=0,
            title="대출채권",
            x=[108, 365, 485, 621, 755],
            y=[
                356,
                385,
                414,
                443,
                473,
                502,
                531,
                560,
                589,
                619,
                648,
                677,
                706,
                735,
                764,
                794,
                823,
                852,
                881,
            ],
            labels=[
                "종류",
                "<상호금융자금대출금>",
                "일반대출금",
                "자립예탁금대출금",
                "종합통장대출금",
                "적금관계대출금",
                "상호금융단기농사대출금",
                "상호금융중기대출금",
                "상호금융특별장기대출금",
                "농업자금우대대출금",
                "소계",
                "<정책자금대출금>",
                "금융농업중기대출금",
                "축산발전기금대출금",
                "농어촌구조개선자금대출금",
                "기타재정시설자금대출금",
                "소계",
                "합계",
            ],
            groups={1: list(range(2, 11)), 11: list(range(12, 17))},
            merges=[(1, 0, 1, 4), (10, 0, 1, 2), (16, 0, 1, 2), (17, 0, 1, 2)],
            header=1,
            agg={10: list(range(2, 10)), 16: list(range(12, 16)), 17: [10, 16]},
        ),
        dict(
            page=45,
            index=0,
            title="고객별 대출채권",
            x=[108, 267, 443, 620],
            y=[425, 458, 490, 523, 555],
            labels=["구분", "개인", "법인", "합계"],
            groups={},
            merges=[],
            header=1,
            agg={3: [1, 2]},
        ),
        dict(
            page=45,
            index=1,
            title="자산건전성 당기말",
            x=[108, 237, 325, 411, 497, 583, 669, 755],
            y=[745, 768, 791, 814, 837, 859, 882, 905, 927, 950],
            labels=[
                "구분",
                "<설정대상채권>",
                "상호금융자금대출금",
                "정책자금대출금",
                "여신성가지급금",
                "신용카드수탁취급계정",
                "합계(A)",
                "<대손충당금잔액>(B)",
                "설정대상채권대비대손충당금비율(B/A)",
            ],
            groups={1: [2, 3, 4, 5, 6]},
            merges=[(7, 1, 1, 6), (8, 0, 1, 6)],
            header=1,
            agg={6: [2, 3, 4, 5]},
            special_rows=[7, 8],
        ),
        dict(
            page=46,
            index=0,
            title="자산건전성 전기말",
            x=[108, 237, 325, 411, 497, 583, 669, 755],
            y=[365, 388, 411, 433, 456, 479, 502, 525, 547, 570],
            labels=[
                "구분",
                "<설정대상채권>",
                "상호금융자금대출금",
                "정책자금대출금",
                "여신성가지급금",
                "신용카드수탁취급계정",
                "합계(A)",
                "<대손충당금잔액>(B)",
                "설정대상채권대비대손충당금비율(B/A)",
            ],
            groups={1: [2, 3, 4, 5, 6]},
            merges=[(7, 1, 1, 6), (8, 0, 1, 6)],
            header=1,
            agg={6: [2, 3, 4, 5]},
            special_rows=[7, 8],
        ),
        dict(
            page=46,
            index=1,
            title="연도별 요적립액·요적립율",
            x=[108, 222, 311, 401, 490, 579, 667, 755],
            y=[669, 691, 714, 737, 759, 782, 805, 827, 850],
            labels=["구분", None, "정상", "요주의", "고정", "회수의문", "추정손실", "합계"],
            groups={},
            merges=[(0, 0, 2, 1), (0, 1, 1, 3), (0, 4, 1, 3)],
            header=2,
            agg={7: [2, 3, 4, 5, 6]},
            ratio_columns=[3, 6],
        ),
        dict(
            page=51,
            index=0,
            title="예수금",
            x=[108, 351, 478, 617, 755],
            y=[
                203,
                233,
                262,
                291,
                320,
                349,
                378,
                407,
                436,
                466,
                495,
                524,
                553,
                582,
                611,
                640,
                669,
                698,
                727,
                757,
            ],
            labels=[
                "종류",
                "<요구불예수금>",
                "보통예탁금",
                "별단예탁금",
                "소계",
                "<저축성예수금>",
                "자립예탁금",
                "자유저축예탁금",
                "기업자유예탁금",
                "정기예탁금",
                "정기적금",
                "장학적금",
                "자유적립적금",
                "농어가목돈마련저축",
                "소계",
                "<자유로부금>",
                "자유로부금",
                "소계",
                "합계",
            ],
            groups={1: [2, 3, 4], 5: list(range(6, 15)), 15: [16, 17]},
            merges=[
                (1, 0, 1, 4),
                (5, 0, 1, 4),
                (4, 0, 1, 2),
                (14, 0, 1, 2),
                (17, 0, 1, 2),
                (18, 0, 1, 2),
            ],
            header=1,
            agg={4: [2, 3], 14: list(range(6, 14)), 17: [16], 18: [4, 14, 17]},
        ),
    ]


def annotate(pdf, s):
    # p51 image displayed with a 192px top crop in the inspection view; restore page coordinates.
    if s["page"] == 51:
        s = {**s, "y": [y + 192 for y in s["y"]]}
    tid = f"manual/p{s['page']}/t{s['index']}"

    def cid(r, c):
        return f"{tid}/r{r}c{c}"

    words = pdf[s["page"] - 1].get_text("words")
    if s["page"] == 43:
        s = {**s, "merges": s["merges"] + [(11, 0, 1, 2)]}
    if s["page"] == 51:
        s = {**s, "merges": [(r, c, rs, 2 if r == 5 else cs) for r, c, rs, cs in s["merges"]]}
    cells = []
    skip = set()
    anchors = {}
    for r in range(len(s["y"]) - 1):
        for c in range(len(s["x"]) - 1):
            if (r, c) in skip:
                continue
            rs, cs = next(((a, b) for rr, cc, a, b in s["merges"] if (r, c) == (rr, cc)), (1, 1))
            for rr in range(r, r + rs):
                for cc in range(c, c + cs):
                    skip.add((rr, cc))
                    anchors[rr, cc] = cid(r, c)
            bbox = [s["x"][c] / 1.5, s["y"][r] / 1.5, s["x"][c + cs] / 1.5, s["y"][r + rs] / 1.5]
            native = [
                w
                for w in words
                if bbox[0] <= (w[0] + w[2]) / 2 < bbox[2] and bbox[1] <= (w[1] + w[3]) / 2 < bbox[3]
            ]
            text = " ".join(w[4] for w in sorted(native, key=lambda w: (round(w[1] / 3), w[0])))
            cells.append(
                {
                    "id": cid(r, c),
                    "row": r,
                    "column": c,
                    "rowspan": rs,
                    "colspan": cs,
                    "bbox": bbox,
                    "text": text,
                    "page": s["page"],
                    "source_word_boxes": [list(w[:4]) for w in native],
                }
            )
    lookup = {c["id"]: c for c in cells}
    for r, label in enumerate(s["labels"]):
        if label is not None and norm(lookup[anchors[r, 0]]["text"]) != norm(label):
            raise ValueError(
                f"Source anchor mismatch {tid} row {r}: {lookup[anchors[r, 0]]['text']} != {label}"
            )
    parents = {
        anchors[r, 0]: next(
            (anchors[g, 0] for g, members in s["groups"].items() if r in members), None
        )
        for r in range(s["header"], len(s["labels"]))
    }
    rows = [{"cell": c, "parent": p, "path": path(c, parents)} for c, p in parents.items()]
    cols = []
    for c in range(1, len(s["x"]) - 1):
        cp = [anchors[0, c]]
        if s["header"] == 2:
            cp.append(anchors[1, c])
        cols.append({"leaf": cp[-1], "path": cp, "column": c})
    mappings = []
    for cell in cells:
        r, c = cell["row"], cell["column"]
        if r < s["header"] or c == 0 or r in s["groups"]:
            continue
        cp = (
            []
            if r in s.get("special_rows", [])
            else next(h["path"] for h in cols if h["column"] == c)
        )
        mappings.append(
            {
                "cell": cell["id"],
                "row_path": path(anchors[r, 0], parents),
                "column_path": cp,
                "text": cell["text"],
                "blank": not cell["text"],
                "column_semantics": "row_defined_scalar_not_quality_bucket"
                if r in s.get("special_rows", [])
                else "header_path",
            }
        )
    scopes = [
        {
            "target": anchors[r, 0],
            "type": "total_of" if r == len(s["labels"]) - 1 else "subtotal_of",
            "members": [anchors[x, 0] for x in members],
            "excluded_columns": s.get("ratio_columns", ([1] if s["page"] in (43, 51) else [])),
            "arithmetic_validated": False,
        }
        for r, members in s["agg"].items()
    ]
    return {
        "id": tid,
        "page": s["page"],
        "index": s["index"],
        "title": s["title"],
        "bbox": [s["x"][0] / 1.5, s["y"][0] / 1.5, s["x"][-1] / 1.5, s["y"][-1] / 1.5],
        "cells": cells,
        "rows": rows,
        "columns": cols,
        "mappings": mappings,
        "aggregation": scopes,
        "unknown_scopes": [
            {
                "target": anchors[r, 0],
                "reason": "No detailed biological-asset members displayed; do not invent them.",
            }
            for r in s.get("unknown_agg", [])
        ],
        "source_annotation": s,
        "review_status": "provisional",
        "independent_human_review": False,
    }


def main():
    registry = read(ROOT / "data/namwon-holdout/registry.json")[0]
    assert digest(registry["path"]) == registry["sha256"]
    run = read(ROOT / "data/namwon-holdout/runs.json")[0]
    pr, sr = Path(run["physical_run"]), Path(run["semantic_run"])
    files = [sr / "structured-before.json", sr / "semantic-overlay.json"]
    frozen = {str(p): digest(p) for p in files}
    with fitz.open(registry["path"]) as pdf:
        golden = [annotate(pdf, s) for s in specs()]
    save(
        "golden.provisional.json",
        {
            "version": "tree-reference/0.1",
            "dataset_role": "development_regression",
            "source_sha256": registry["sha256"],
            "authoring": "Agent original-image inspection; handwritten grid coordinates and hierarchy; PDF native words used only to populate text inside independently marked regions. Predictions used afterward for evaluation binding only.",
            "previous_qa_review_does_not_validate_tree": True,
            "tables": golden,
            "cross_page": [
                {
                    "from": "manual/p45/t1",
                    "to": "manual/p46/t0",
                    "relation": "same_note_comparative_period",
                    "row_continuation": False,
                    "from_period": "2025",
                    "to_period": "2024",
                    "review_status": "provisional",
                }
            ],
        },
    )
    st = {s["table_id"]: s for s in read(files[0])}
    overlay = read(files[1])
    feature_cells = {
        f["node_id"]: f["source_cell_ids"][0]
        for f in overlay["features"]
        if len(f["source_cell_ids"]) == 1
    }
    rule = {
        feature_cells[n["semantic_node_id"]]: feature_cells.get(
            n["semantic_parent_id"], n["semantic_parent_id"]
        )
        for n in overlay["table_nodes"]
        if n["semantic_node_id"] in feature_cells and n["semantic_parent_id"]
    }
    all_results = {"Rule-only": [], "Hybrid-C1.1-replay": []}
    for g in golden:
        pp = pr / f"physical/page-{g['page']:03}-table-{g['index']:03}.json"
        frozen[str(pp)] = digest(pp)
        physical = read(pp)
        if iou(g["bbox"], box(physical["bounding_box"])) < 0.8:
            raise ValueError("Table binding mismatch")
        g["prediction_table_id"] = physical["table_id"]
        all_results["Rule-only"].append(evaluate_table(g, physical, st[physical["table_id"]], rule))
        rp = ROOT / f"data/semantic-c11-replay/namwon-p{g['page']:03}-hybrid.json"
        if rp.exists():
            frozen[str(rp)] = digest(rp)
            replay = read(rp)
            features = {
                f["node_id"]: f["source_cell_ids"][0]
                for f in replay["registry"]["features"]
                if len(f["source_cell_ids"]) == 1
            }
            parents = {
                features[c]: features.get(p, p)
                for c, p in replay["revision"]["after"].items()
                if c in features and p
            }
            all_results["Hybrid-C1.1-replay"].append(
                evaluate_table(g, physical, st[physical["table_id"]], parents)
            )
    common = {r["table_id"] for r in all_results["Hybrid-C1.1-replay"]}
    report = {
        "dataset": "Namwon development diagnostic; not generalization",
        "reference_status": "provisional; independent tree review pending",
        "external_calls": 0,
        "methods": {
            m: {
                "tables": rs,
                "micro": micro(rs),
                "common_micro": micro([r for r in rs if r["table_id"] in common]),
                "error_types": dict(
                    Counter((i["stage"] + " / " + i["code"]) for r in rs for i in r["issues"])
                ),
            }
            for m, rs in all_results.items()
        },
        "Text-Structure-Context-v1-Hybrid": {
            "accuracy": None,
            "reason": "No executed live result; prepared requests are not predictions.",
        },
        "aggregation_scope": {
            "accuracy": None,
            "annotated_scopes": sum(len(g["aggregation"]) for g in golden),
            "reason": "Existing saved aggregation sidecars contain no scope predictions for these tables; coverage 0, unsupported/unimplemented scope accuracy N/A.",
        },
        "cross_page": {
            "accuracy": None,
            "annotated_pairs": 1,
            "positive_row_continuation_pairs": 0,
            "reason": "No comparable saved typed cross-page relation; one negative row-continuation / positive same-note comparison reference only.",
        },
        "evidence_metric_definition": "Cell text (whitespace normalized), page, and bbox IoU >=0.7; does NOT establish semantic correctness or exact typography.",
        "frozen_inputs": frozen,
        "source_pdf_unchanged": digest(registry["path"]) == registry["sha256"],
        "input_hashes_unchanged": all(digest(p) == h for p, h in frozen.items()),
    }
    save("results.json", report)
    save(
        "source-bindings.json",
        {m: {r["table_id"]: r["bindings"] for r in rs} for m, rs in all_results.items()},
    )
    # Offline review: original crop and expected/predicted side by side, with every issue trace.
    sections = []
    for g in golden:
        cells = {c["id"]: c for c in g["cells"]}

        def pretty(v):
            def readable(x):
                if isinstance(x, str) and x in cells:
                    c = cells[x]
                    return f"{c['text'] or '(공란)'} [행{c['row'] + 1}, 열{c['column'] + 1}]"
                if isinstance(x, list):
                    return [readable(a) for a in x]
                if isinstance(x, dict):
                    return {k: readable(a) for k, a in x.items()}
                return x

            return json.dumps(readable(v), ensure_ascii=False, indent=2)

        rows = []
        for method, results in all_results.items():
            rr = next((r for r in results if r["table_id"] == g["id"]), None)
            if rr is None:
                rows.append(f"<p>{escape(method)}: N/A — 해당 페이지 실행 없음</p>")
                continue
            rows.append(f"<h3>{escape(method)}</h3><p>{escape(pretty(rr['metrics']))}</p>")
            for n, issue in enumerate(rr["issues"]):
                c = cells.get(issue["target"])
                bbox = c["bbox"] if c else g["bbox"]
                x0, y0, x1, y1 = bbox
                vx = max(0, x0 - 35)
                vy = max(0, y0 - 22)
                vw = min(595 - vx, max(200, x1 - x0 + 70))
                vh = max(60, y1 - y0 + 44)
                svg = f'<svg viewBox="{vx} {vy} {vw} {vh}"><image href="../rag-phase3/review/p{g["page"]}.png" width="595.32" height="841.92"/><rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" fill="#f4bc3230" stroke="#b67400" stroke-width="1"/></svg>'
                rows.append(
                    f'<details><summary>{escape(issue["stage"])} · {escape(issue["code"])} · {escape(c["text"] if c else issue["target"])}</summary><div class="compare"><div>{svg}</div><div><b>원본 기준 제안</b><pre>{escape(pretty(issue["expected"]))}</pre><b>실제 예측</b><pre>{escape(pretty(issue["predicted"]))}</pre><p>셀: {escape(issue["target"])}</p></div></div></details>'
                )
        labels = {c["id"]: c["text"] or "(공란)" for c in g["cells"]}
        rows_tree = "\n".join(" → ".join(labels.get(n, n) for n in r["path"]) for r in g["rows"])
        cols_tree = "\n".join(" → ".join(labels.get(n, n) for n in c["path"]) for c in g["columns"])
        scopes = "\n".join(
            labels[a["target"]] + " ← " + ", ".join(labels[n] for n in a["members"])
            for a in g["aggregation"]
        )
        sections.append(
            f'<section id="p{g["page"]}t{g["index"]}"><h2>p{g["page"]} · {escape(g["title"])}</h2><div class="compare"><div><img src="../rag-phase3/review/p{g["page"]}.png" alt="원본 {g["page"]}페이지"></div><div><h3>검토용 행 경로</h3><pre>{escape(rows_tree)}</pre><h3>열 경로</h3><pre>{escape(cols_tree)}</pre><h3>집계 범위 (재계산 검증 아님)</h3><pre>{escape(scopes)}</pre><p>사람의 Tree 검토 전 provisional입니다. 아래 오류는 이 제안 정답과의 차이입니다.</p></div></div>'
            + "".join(rows)
            + "</section>"
        )
    nav = " · ".join(
        f'<a href="#p{g["page"]}t{g["index"]}">p{g["page"]} {escape(g["title"])}</a>'
        for g in golden
    )
    (OUT / "index.html").write_text(
        '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>남원 Tree 진단</title><style>body{font:16px/1.65 system-ui;margin:24px;background:#f4f6f3;color:#23382e}section{background:white;padding:24px;margin:24px 0}nav{position:sticky;top:0;background:#e8f2ed;padding:14px}a{color:#18604c}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px}.compare{display:grid;grid-template-columns:1fr 1fr;gap:24px}img,svg{width:100%}details{border:1px solid #ccd8d0;padding:12px;margin:10px 0}summary{cursor:pointer}h1{font-size:26px}@media(max-width:800px){.compare{grid-template-columns:1fr}}</style><h1>남원축협 Tree 정확도 진단 · 개발셋</h1><p>7개 표의 원본과 수기 구조를 비교합니다. 독립 사람 검토 전 provisional이며 일반화 성능이 아닙니다. 오류 항목을 펼치면 원본 셀과 예측 차이를 볼 수 있습니다.</p><nav>'
        + nav
        + "</nav>"
        + "".join(sections)
        + "</html>",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                m: {"micro": v["micro"], "errors": v["error_types"]}
                for m, v in report["methods"].items()
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
