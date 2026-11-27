"""Offline corpus QA, duplicate inventory and reporting only."""

import hashlib
import html
import json
from pathlib import Path
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/namwon-holdout"


def read(p):
    return json.loads(p.read_text(encoding="utf8"))


def main():
    registry = read(OUT / "registry.json")
    runs = read(OUT / "runs.json")
    gold = read(OUT / "golden-review.json")
    frozen = read(OUT / "frozen-baseline.json")
    changed = [
        p
        for p, s in frozen["files"].items()
        if hashlib.sha256((ROOT / p).read_bytes()).hexdigest() != s
    ]
    assert not changed
    assert len(runs) == 7 and all(not r["semantic"]["calls"] for r in runs)
    raster_hashes = {}
    duplicate_pages = []
    inventory = []
    for r, run in zip(registry, runs, strict=True):
        assert r["slug"] == run["slug"]
        assert hashlib.sha256(Path(r["path"]).read_bytes()).hexdigest() == r["sha256"]
        with pymupdf.open(r["path"]) as doc:
            rotations = []
            for p in doc:
                rotations.append(p.rotation)
                h = hashlib.sha256(
                    p.get_pixmap(dpi=36, colorspace=pymupdf.csGRAY, alpha=False).samples
                ).hexdigest()
                if h in raster_hashes:
                    duplicate_pages.append(
                        dict(
                            first=raster_hashes[h],
                            second=[r["slug"], p.number + 1],
                            method="exact_36dpi_grayscale_hash",
                        )
                    )
                else:
                    raster_hashes[h] = [r["slug"], p.number + 1]
        p = run["physical"]
        inventory.append(
            dict(
                slug=r["slug"],
                pages=r["pages"],
                native_chars=r["native_chars"],
                native_words=r["native_words"],
                sha256=r["sha256"],
                rotations=rotations,
                physical_candidates=p["detected_table_regions"],
                geometric_pass=p["automatic_structure_pass"],
                partial=p["partial_reconstruction"],
                failed_or_review=p["failed_or_review_required"],
                no_grid_pages=p["pages_without_detected_grid"],
                semantic_nodes=run["semantic"]["table_nodes"],
                status=run["semantic"]["status"],
                physical_accuracy="not independently validated",
                ocr_needed=r["native_chars"] == 0,
            )
        )
    overlap = [
        dict(
            scan="scan-1",
            scan_pages=[1],
            native_pages=[6, 7],
            status="visual_partial_content_overlap_not_exact_duplicate",
            evidence="Same current/prior cash and current-assets totals, deposit groups; scan is parallel panels with additional blank accounts. Only first-page anchors compared.",
        ),
        dict(
            scan="scan-2",
            scan_pages=[1],
            native_pages=[9],
            status="visual_partial_content_overlap_not_exact_duplicate",
            evidence="Same operating revenue and interest revenue anchors; scan includes extra zero/blank accounts and changed numbering.",
        ),
        dict(
            scan="scan-3",
            scan_pages=[1],
            native_pages=[60],
            status="visual_content_overlap_not_exact_duplicate",
            evidence="Same retained earnings, net income, dividend and carry-forward amounts; presentation/blank rows differ.",
        ),
        dict(
            scan="scan-4",
            scan_pages=[1, 2],
            native_pages=[12],
            status="candidate_overlap_first_page_visually_checked",
            evidence="2024 opening/closing and equity movements match upper portion of native p12; scan p2 correspondence requires further review.",
        ),
        dict(
            scan="scan-5",
            scan_pages=[1],
            native_pages=[3, 4, 5],
            status="different_document_not_duplicate",
            evidence="Internal auditors opinion versus external accountant report; not interchangeable.",
        ),
        dict(
            scan="scan-6",
            scan_pages=[1, 13, 22],
            native_pages=[],
            status="different_period_not_duplicate",
            evidence="Cover/contact-sheet review: p1 March, p13 June, p22 September reports; not annual December figures.",
        ),
    ]
    posthoc = [
        dict(
            id="posthoc-loan-grand-total",
            page=43,
            observed="기타재정시설자금대출금 → 합계",
            expected="Grand total is table-level; no individual-loan parent",
            category="over_hierarchy",
            review="source-confirmed by agent after prediction exposure; outside frozen primary score",
        ),
        dict(
            id="posthoc-deposit-grand-total",
            page=51,
            observed="자유로부금 (detail account) → 합계",
            expected="Grand total covers all deposit groups; not the final detail account",
            category="over_hierarchy",
            review="source-confirmed by agent after prediction exposure; outside frozen primary score",
        ),
    ]
    summary = dict(
        documents=inventory,
        exact_document_duplicate_pairs=[
            [a["slug"], b["slug"]]
            for i, a in enumerate(registry)
            for b in registry[i + 1 :]
            if a["sha256"] == b["sha256"]
        ],
        exact_raster_duplicate_pages=duplicate_pages,
        content_overlap=overlap,
        posthoc_findings=posthoc,
        frozen_file_count=len(frozen["files"]),
        changed_frozen_files=changed,
        actual_external_calls=0,
        metrics=gold["metrics"],
        mining_counts={k: len(v) for k, v in gold["mining"].items()},
    )
    (OUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf8"
    )
    extra = [
        "<h2>7개 문서 처리 상태</h2><table><tr><th>문서</th><th>페이지</th><th>Native 문자</th><th>물리 후보/기하 통과/부분/실패·검토</th><th>원본·결과</th></tr>"
    ]
    for d, run in zip(inventory, runs, strict=True):
        links = f'<a href="{Path(run["physical_run"]).as_uri()}/physical/index.html">물리 전체</a> · <a href="{Path(run["semantic_run"]).as_uri()}/index.html">계층 전체</a>'
        if d["slug"] != "audit-native":
            links += f' · <a href="review/{d["slug"]}-first.png">첫 페이지</a>'
        extra.append(
            f"<tr><td>{d['slug']}</td><td>{d['pages']}</td><td>{d['native_chars']}</td><td>{d['physical_candidates']}/{d['geometric_pass']}/{d['partial']}/{d['failed_or_review']}</td><td>{links}</td></tr>"
        )
    extra += [
        "</table><p>기하 통과는 원본 구조 정확도 입증이 아닙니다. OCR/LLM은 실행하지 않았습니다.</p>",
        "<h2>예측 확인 후 별도 발견 · 기본 지표 제외</h2><pre>"
        + html.escape(json.dumps(posthoc, ensure_ascii=False, indent=2))
        + "</pre>",
        "<h2>중복/기간 대응 · 부분 시각 검토</h2><pre>"
        + html.escape(json.dumps(overlap, ensure_ascii=False, indent=2))
        + "</pre>",
        '<p><a href="summary.json">전체 집계 JSON</a> · <a href="pilot-plan/manifest.json">미실행 LLM 파일럿 계획</a> · <a href="review/scan6-contact.png">별첨6 전체 페이지 미리보기</a></p>',
    ]
    index = OUT / "index.html"
    content = index.read_text(encoding="utf8").split("<!-- corpus-summary -->")[0].replace("</html>", "")
    index.write_text(content + "\n<!-- corpus-summary -->\n" + "\n".join(extra) + "</html>", encoding="utf8")
    print(
        json.dumps(
            {
                k: summary[k]
                for k in (
                    "frozen_file_count",
                    "changed_frozen_files",
                    "mining_counts",
                    "exact_raster_duplicate_pages",
                )
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
