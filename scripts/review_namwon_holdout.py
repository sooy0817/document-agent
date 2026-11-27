"""Source-anchored selective evaluation. No production rules, OCR or external calls."""

# ruff: noqa: E402
import hashlib
import html
import json
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/namwon-holdout"
sys.path.insert(0, str(ROOT / "src"))


def read(p):
    return json.loads(p.read_text(encoding="utf8"))


def norm(s):
    return "".join(unicodedata.normalize("NFKC", s).split())


def main():
    gold = read(OUT / "source-annotations.json")
    registry = read(OUT / "registry.json")
    run = next(r for r in read(OUT / "runs.json") if r["slug"] == "audit-native")
    overlay = read(Path(run["semantic_run"]) / "semantic-overlay.json")
    features = {f["node_id"]: f for f in overlay["features"]}
    nodes = {n["semantic_node_id"]: n for n in overlay["table_nodes"]}
    doc = pymupdf.open(registry[0]["path"])
    source_lines = {}
    for page in doc:
        source_lines[page.number + 1] = [
            dict(text="".join(s["text"] for s in line["spans"]), bbox=line["bbox"])
            for b in page.get_text("dict")["blocks"]
            if b["type"] == 0
            for line in b["lines"]
        ]

    def anchor(page, text, occurrence=0):
        matches = [line for line in source_lines[page] if norm(line["text"]) == norm(text)]
        if len(matches) <= occurrence:
            raise ValueError(f"SOURCE_SELECTOR_NOT_FOUND: p{page} {text} ({len(matches)})")
        line = matches[occurrence]
        fs = [
            f
            for f in features.values()
            if f["axis"] == "row" and f["page_number"] == page and norm(f["text_raw"]) == norm(text)
        ]
        cy = (line["bbox"][1] + line["bbox"][3]) / 2
        fs = [
            f
            for f in fs
            if any(r["bbox"]["y0"] - 2 <= cy <= r["bbox"]["y1"] + 2 for r in f["source_refs"])
        ]
        return dict(
            page=page,
            text=line["text"],
            bbox=line["bbox"],
            feature_ids=[f["node_id"] for f in fs],
            source_cell_ids=[i for f in fs for i in f["source_cell_ids"]],
            node_id=fs[0]["node_id"] if len(fs) == 1 else None,
        )

    cases = []
    for negative, key in ((False, "relations"), (True, "negative_relations")):
        for c in gold[key]:
            parent = anchor(c["page"], c["parent"], c.get("parent_occurrence", 0))
            child = anchor(c.get("child_page", c["page"]), c["child"], c.get("child_occurrence", 0))
            n = nodes.get(child["node_id"])
            predicted_parent = n["semantic_parent_id"] if n else None
            kind = c.get("relation_type", "parent_child")
            hit = bool(
                parent["node_id"]
                and predicted_parent == parent["node_id"]
                and (negative or n["relation_type"] == kind)
            )
            status = (
                ("forbidden_edge_present" if hit else "forbidden_edge_absent")
                if negative
                else (
                    "correct"
                    if hit
                    else "source_node_not_mapped"
                    if not parent["node_id"] or not child["node_id"]
                    else "wrong_parent_or_relation"
                    if predicted_parent
                    else "missing_parent"
                )
            )
            ds = [d for d in overlay["decisions"] if d["proposal"]["child_id"] == child["node_id"]]
            cases.append(
                dict(
                    **c,
                    negative=negative,
                    parent_anchor=parent,
                    child_anchor=child,
                    status=status,
                    predicted_parent=features.get(predicted_parent, {}).get("text_raw"),
                    predicted_relation=n["relation_type"] if n else None,
                    predicted_path=[features[i]["text_raw"] for i in n["hierarchy_path"]]
                    if n
                    else [],
                    node=n,
                    decisions=ds,
                )
            )
    paths = []
    for p in gold["paths"]:
        aa = [anchor(p["page"], s) for s in p["labels"]]
        ids = [a["node_id"] for a in aa]
        n = nodes.get(ids[-1])
        actual = n["hierarchy_path"] if n else []
        correct = all(ids) and ids[0] in actual and actual[actual.index(ids[0]) :] == ids
        paths.append(
            dict(
                **p,
                anchors=aa,
                correct=bool(correct),
                actual=[features[i]["text_raw"] for i in actual],
            )
        )
    positive = [c for c in cases if not c["negative"]]
    parent_cases = [c for c in positive if c.get("relation_type", "parent_child") == "parent_child"]
    correct = sum(c["status"] == "correct" for c in parent_cases)
    predicted = sum(bool(c["node"] and c["node"]["semantic_parent_id"]) for c in parent_cases)
    metrics = dict(
        status=gold["status"],
        source_annotation_sha256=hashlib.sha256(
            (OUT / "source-annotations.json").read_bytes()
        ).hexdigest(),
        parent_relation=dict(
            correct=correct,
            expected=len(parent_cases),
            predicted_on_annotated_targets=predicted,
            precision=correct / predicted if predicted else None,
            recall=correct / len(parent_cases),
            denominator="only selected child targets with independently source-authored parents; not all document edges",
        ),
        typed_relation_status=dict(Counter(c["status"] for c in positive)),
        negative_checks=dict(Counter(c["status"] for c in cases if c["negative"])),
        exact_paths=dict(correct=sum(p["correct"] for p in paths), total=len(paths)),
        full_document_precision=None,
        full_document_recall=None,
        llm_uplift=None,
        actual_llm_calls=0,
    )
    mining = defaultdict(list)
    for f in features.values():
        n = nodes.get(f["node_id"])
        if n and len(n["candidate_parent_ids"]) > 1:
            mining["multiple_parent_candidates"].append(
                dict(
                    node_id=f["node_id"],
                    page=f["page_number"],
                    label=f["text_raw"],
                    parents=n["candidate_parent_ids"],
                    status="candidate_not_verified_error",
                )
            )
    for d in overlay["decisions"]:
        for reason in d["reasons"]:
            if reason in ("NUMBERED_SCOPE_ENDED", "UNSUPPORTED_CROSS_PAGE"):
                mining[reason].append(d)
    payload = dict(
        metrics=metrics,
        cases=cases,
        paths=paths,
        scope_reviews=gold["scope_reviews"],
        mining=dict(mining),
    )
    (OUT / "golden-review.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf8"
    )
    esc = html.escape
    parts = [
        '<!doctype html><html lang="ko"><meta charset="utf-8"><title>남원축협 Holdout 검토</title><style>body{font:16px system-ui;max-width:1400px;margin:30px auto;padding:20px;color:#17222d;background:#f6f7f9}article{background:white;border:1px solid #ccd3dd;padding:20px;margin:20px 0}img{width:48%;vertical-align:top}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#16499a}summary{cursor:pointer}table{border-collapse:collapse}td,th{border:1px solid #ccd3dd;padding:8px}</style><h1>남원축협 · Rule-only 신규 Holdout</h1><p>독립 사람 검토 대기 · 정답은 원본 이미지에서 별도 작성 · 선택적 평가 · 실제 LLM 호출 0회</p>',
        '<p><a href="source-annotations.json">원본 기대값</a> · <a href="golden-review.json">검토 JSON</a> · <a href="registry.json">7개 문서 SHA/페이지/텍스트</a> · <a href="runs.json">전체 실행 목록</a></p>',
        f'<p><a href="{Path(run["semantic_run"]).as_uri()}/index.html">65페이지 전체 실제 계층 출력</a> · <a href="{Path(run["physical_run"]).as_uri()}/physical/index.html">물리 복원 전체</a></p>',
        "<pre>" + esc(json.dumps(metrics, ensure_ascii=False, indent=2)) + "</pre>",
    ]
    for c in cases:
        p = c["page"]
        cp = c.get("child_page", p)
        parts.append(
            f'<article id="{c["id"]}"><h2>{esc(c["id"])} · {esc(c["status"])}</h2><p>PDF p{p}→p{cp} · {esc(c["category"])}</p><p>원본 기대: {esc(c["parent"])} → {esc(c["child"])} ({esc(c.get("relation_type", "parent_child"))})</p><p>실제: {esc(str(c["predicted_parent"]))} → {esc(c["child"])} · {esc(str(c["predicted_relation"]))}</p><p>실제 경로: {esc(" → ".join(c["predicted_path"]))}</p>'
        )
        parts.append(
            f'<details><summary>원본 / 물리 오버레이 및 좌표</summary><img src="review/audit-p{cp:03}.png"><img src="{Path(run["physical_run"]).as_uri()}/physical/page-{cp:03}-overlay.png"><pre>{esc(json.dumps(dict(parent=c["parent_anchor"], child=c["child_anchor"]), ensure_ascii=False, indent=2))}</pre></details></article>'
        )
    parts.append(
        "<h2>주석 적용 범위 · 별도 사람 검토</h2><pre>"
        + esc(json.dumps(gold["scope_reviews"], ensure_ascii=False, indent=2))
        + "</pre></html>"
    )
    (OUT / "index.html").write_text("\n".join(parts), encoding="utf8")
    print(json.dumps(metrics, ensure_ascii=True))
    for c in cases:
        print(c["id"], c["status"], repr(c["predicted_parent"]), flush=True)


if __name__ == "__main__":
    main()
