"""Read-only Canonical replay, opt-in semantic gate and source-linked report.

Gold is evaluated only after all detector results have been written. No API/DB.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from html import escape
import json
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from doc_understanding.domain.pipeline_contracts import CanonicalDocument
from doc_understanding.parser.physical.diagram_gate import classify_page, analyze_allowed
from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer

BASE = Path("reports/containment-generalization-20261026")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def box(value):
    return list(value.values()) if isinstance(value, dict) else value


def area(b):
    return max(b[2] - b[0], 0) * max(b[3] - b[1], 0)


def fraction(candidate, region):
    a, b = box(candidate), box(region)
    intersection = [max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])]
    return area(intersection) / max(area(a), 1e-9)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=False)
    inputs = {
        p.parent.name: p
        for p in sorted((BASE / "runs").glob("*/canonical.json"))
        if p.parent.name.startswith(("KD", "HK"))
    }
    inputs.update(
        {
            "R-NH": Path("data/document-rag/gangdong/document-v2.json"),
            "R-PNEUMA": Path("data/physical-recovery/dev-v2/pneuma/S0/canonical.json"),
            "R-LH": Path("reports/lh-full-baseline/run-20261026-150231/canonical-document.json"),
        }
    )
    frozen = list(inputs.values()) + list(Path("src").rglob("*.py"))
    frozen += [
        Path(__file__),
        Path("tests/test_diagram_gate.py"),
        BASE / "korean.gold.json",
        BASE / "gold-errata.json",
        BASE / "comparison-reviewed.html",
    ]
    hashes = {p.as_posix(): sha(p) for p in frozen}
    write(
        out / "freeze.json",
        dict(
            created=datetime.now(timezone.utc).isoformat(),
            scope="All reused development/regression; former holdout is NOT unseen in this experiment",
            files=hashes,
            inputs={k: p.as_posix() for k, p in inputs.items()},
            semantic_execution="Only continue_semantic_candidate IDs are passed to existing analyzer",
            production_enabled=False,
            api_calls=0,
            db_writes=0,
        ),
    )
    documents = []
    for key, path in inputs.items():
        start = perf_counter()
        doc = CanonicalDocument.model_validate_json(path.read_text(encoding="utf8"))
        before = hashlib.sha256(doc.model_dump_json().encode()).hexdigest()
        pages, semantic, errors = [], [], []
        for page in doc.document_ir.pages:
            tables = tuple(t for t in doc.physical_tables if t.page_number == page.page_number)
            gate = classify_page(page, tables)
            pages.append(gate.model_dump(mode="json"))
            # Actual existing semantic analyzer is called through the gate, never
            # on a skipped/held table. Keep per-table errors and continue document.
            for t in tables:
                decision = next(d for d in gate.decisions if d.table_id == t.table_id)
                single_gate = gate.model_copy(update={"decisions": (decision,)})
                try:
                    semantic.extend(
                        s.model_dump(mode="json")
                        for s in analyze_allowed((t,), single_gate, SemanticTableAnalyzer())
                    )
                except Exception as exc:
                    errors.append(
                        dict(
                            table_id=t.table_id,
                            stage="semantic",
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    )
        after = hashlib.sha256(doc.model_dump_json().encode()).hexdigest()
        assert before == after, "SOURCE_MUTATED"
        decisions = [d for p in pages for d in p["decisions"]]
        ids = {s["table_id"] for s in semantic}
        allowed = {d["table_id"] for d in decisions if d["action"] == "continue_semantic_candidate"}
        # StructuredTableIR uses a different ID space; call routing tested separately.
        source_blocks = {b.block_id for p in doc.document_ir.pages for b in p.blocks}
        source_cells = {c.cell_id for t in doc.physical_tables for c in t.cells}
        assert all(
            c["from_cell_id"] in source_cells
            and c["to_cell_id"] in source_cells
            and c["source_block_id"] in source_blocks
            for d in decisions
            for c in d["connectors"]
        )
        summary = dict(
            id=key,
            input=path.as_posix(),
            pages=len(pages),
            physical_candidates=len(decisions),
            actions=dict(Counter(d["action"] for d in decisions)),
            semantic_calls=len(allowed),
            semantic_outputs=len(semantic),
            errors=errors,
            source_json_unchanged=True,
            elapsed_seconds=perf_counter() - start,
        )
        write(out / key / "gate.json", dict(document_id=doc.document_id, pages=pages))
        write(out / key / "structured-replay.json", semantic)
        write(out / key / "summary.json", summary)
        documents.append(summary)
        print(key, summary["actions"], "errors", len(errors), flush=True)
        del doc, semantic, ids

    # Evaluation is a separate stage; Gold/labels are never detector inputs.
    gold = json.loads((BASE / "korean.gold.json").read_text(encoding="utf8"))
    cases, parts = [], []
    for case in gold["records"]:
        key, pg = case["id"], case["page"]
        result = json.loads((out / key / "gate.json").read_text(encoding="utf8"))
        ds = next(p["decisions"] for p in result["pages"] if p["page_number"] == pg)
        selected = [d for d in ds if fraction(d["bbox"], case["bbox"]) >= 0.5]
        cases.append(
            dict(
                case_id=case["case_id"],
                id=key,
                page=pg,
                label=case["label"],
                original_split=case["split"],
                split="reused_regression",
                kind=case["kind"],
                candidates=len(selected),
                actions=dict(Counter(d["action"] for d in selected)),
                decisions=selected,
                no_candidate="upstream_missing_or_no_table_candidate_not_gate_success"
                if not selected
                else None,
            )
        )
        evidence = json.loads(
            (BASE / "korean-evidence" / f"{case['case_id']}.json").read_text(encoding="utf8")
        )
        for part in case.get("parts", []):
            scored = []
            for t in evidence["physical_tables"]:
                matches = sum(
                    any(
                        max(abs(a - b) for a, b in zip(box(c["source_bbox"]), g["bbox"])) <= 1.5
                        for c in t["cells"]
                    )
                    for g in part["cells"]
                )
                scored.append((matches, t["table_id"]))
            best = max((v for v in scored if v[0] > 0), default=(0, None))
            match = next((d for d in ds if d["table_id"] == best[1]), None)
            parts.append(
                dict(
                    case_id=part["case_id"],
                    document=key,
                    page=pg,
                    gold_cells=len(part["cells"]),
                    original_matching_cells=best[0],
                    table_id=best[1],
                    action=match["action"] if match else "upstream_no_matching_cells",
                    classification=match["classification"] if match else None,
                )
            )
    changed = [p for p, h in hashes.items() if sha(Path(p)) != h]
    assert not changed, changed
    result = dict(
        documents=documents,
        cases=cases,
        gold_parts=parts,
        gold_part_actions=dict(Counter(p["action"] for p in parts)),
        diagram_cases=[
            dict(case_id=c["case_id"], candidates=c["candidates"], actions=c["actions"])
            for c in cases
            if c["kind"] == "negative_diagram"
        ],
        preservation=dict(files_checked=len(hashes), changed_files=changed),
        limits=[
            "Previously inspected material; no independent generalization claim",
            "Gold is partial and single-agent reviewed; known Gold errata retained",
            "Shared edges do not certify table semantics; missed diagrams can still continue",
            "Unknown holds can block real tables; evaluate separately from diagram exclusions",
            "No new PDF extraction, OCR, embeddings, search or answer validation",
            "No table candidate is upstream absence, not successful diagram rejection",
        ],
        api_calls=0,
        api_cost=0,
        production_enabled=False,
    )
    write(out / "evaluation.json", result)
    build_report(out, result)


def build_report(out, result):
    # Reuse the existing report's static styling, no new application/UI framework.
    prior = (BASE / "comparison-reviewed.html").read_text(encoding="utf8")
    style = prior.split("<style>", 1)[1].split("</style>", 1)[0]

    def link(p):
        import os

        return Path(os.path.relpath(p, out)).as_posix()

    def dump(v):
        return escape(json.dumps(v, ensure_ascii=False, indent=2))

    html = [
        '<!doctype html><html lang="ko"><meta charset="utf-8"><title>도식 보류 규칙 실험</title>',
        f"<style>{style}</style><body><h1>도식 후보의 표 의미 분석 보류 실험</h1>",
        "<p>기존 한국어 12문서와 농협·PNEUMA·LH를 재사용한 회귀 실험입니다. 이전 Holdout도 이번에는 미사용 자료가 아닙니다. 운영 적용은 꺼져 있습니다.</p>",
        "<p>서로 떨어진 폐쇄 셀 사이의 연결선이 여러 개이면 도식 후보로 보류합니다. 공통 경계를 공유하는 셀은 표 후보로 기존 분석기를 호출합니다. 판단 근거가 부족하면 unknown으로 보류합니다. 원본 JSON과 텍스트는 그대로 보존합니다.</p>",
        "<p>이 규칙은 선/셀 후보를 이미 추출한 뒤 의미 Tree 생성 직전에 실행합니다. 화살표의 절차 의미, 이미지 도식, OCR은 처리하지 않습니다.</p>",
        '<p><a href="evaluation.json">전체 평가 JSON</a> · <a href="freeze.json">실행 전 동결 / 입력 해시</a></p>',
        "<h2>원본 표 부분 Gold에 대응하는 후보의 처리</h2><pre>"
        + dump(result["gold_part_actions"])
        + "</pre>",
        "<p>정상 표의 skip은 잘못된 도식 제외, hold는 불필요 보류입니다. upstream은 원래 물리 복원 실패이며 이번 보류기의 성공으로 세지 않습니다. 부분 Gold가 대응하는 최다 셀 일치 후보 기준이며 문서 전체 정확도가 아닙니다.</p>",
        "<h2>기존 도식 6개 구간</h2><pre>" + dump(result["diagram_cases"]) + "</pre>",
        "<h2>문서별 실행</h2><table><tr><th>문서</th><th>쪽/후보</th><th>처리 건수</th><th>실제 의미 분석</th></tr>",
    ]
    for d in result["documents"]:
        html.append(
            f"<tr><td>{d['id']}</td><td>{d['pages']}/{d['physical_candidates']}</td><td>{dump(d['actions'])}</td><td>{d['semantic_outputs']}/{d['semantic_calls']} · 오류 {len(d['errors'])}</td></tr>"
        )
    html.append("</table><h2>원본과 실제 판단 근거</h2>")
    for c in result["cases"]:
        image = BASE / "korean-source-images" / c["id"] / f"full-{c['page']}.png"
        evidence = BASE / "korean-evidence" / f"{c['case_id']}.json"
        html.append(
            f'<details><summary>{c["case_id"]} · {c["id"]} p.{c["page"]} · {escape(c["label"])}</summary><div class="pair"><figure><a href="{link(image)}"><img loading="lazy" src="{link(image)}"></a></figure><div><pre>{dump(c)}</pre></div></div><p><a href="{link(evidence)}">기존 전체 Physical / Structured / Cell Mapping</a> · <a href="{c["id"]}/gate.json">새 판단 sidecar</a> · <a href="{c["id"]}/structured-replay.json">허용 후보만 실제 재실행한 Structured</a></p></details>'
        )
    html.append(
        "<h2>검증 한계와 보존</h2><p>원본 PDF, Canonical, Physical, 기존 Structured 및 이전 보고서를 변경하지 않았습니다. 보류된 표의 텍스트도 원본에 남습니다. 검색 인덱스와 RAG에는 아직 연결하지 않았으므로 검색 기능 검증 결과가 아닙니다.</p><pre>"
        + dump(result["preservation"])
        + "</pre><p>API 호출 0회 · API 비용 0 · DB 쓰기 0. 결과를 본 뒤 규칙을 조정하지 않았습니다.</p></body></html>"
    )
    with (out / "comparison.html").open("x", encoding="utf8") as f:
        f.write("\n".join(html))


if __name__ == "__main__":
    main()
