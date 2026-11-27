"""셀 매핑 평가: 매핑 테스트셋(config/evaluation/mapping-dev-v1.json)으로 표 구조 분석 결과를 채점한다. 모델 호출 없음.

- 기본: 코퍼스(DB)에 적재된 최신 문서를 채점한다(현재 기준선).
- --candidate: CanonicalDocument JSON 파일들(후보 구조 분석 실행)을 채점한다. 기준선 문서는 바꾸지 않는다.
- --review-originals: 코퍼스 문서의 검토 전 원본에 현재 코드의 구조 검토(규칙만, 모델 없음)를 다시 적용해 채점한다.
  DB·저장 파일은 바꾸지 않는다(규칙 변경의 전후 비교용).
결과: --out 폴더(기본 work/mapping-eval/<세트>-<시각>/)에 report.json, report.md.
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

from doc_understanding.evaluation import failure_log
from doc_understanding.evaluation.mapping import failures as mapping_failures
from doc_understanding.evaluation.mapping.dataset import load
from doc_understanding.evaluation.mapping.score import score
from doc_understanding.evaluation.rag.snapshot import digest
from doc_understanding.rag.evidence.inspect import document_views
from doc_understanding.services.corpus_rag import corpus_documents

KEYS = ("role_accuracy", "answerable", "exact")


def commit():
    """현재 코드 버전(짧은 커밋 해시, 작업 중 변경이 있으면 +). 장부에서 어느 코드의 결과인지 구분한다."""
    import subprocess
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True,
                               timeout=10).stdout.strip()
        return head + ("+" if dirty else "") if head else None
    except Exception:
        return None


def candidate_documents(paths):
    from doc_understanding.domain.pipeline_contracts import CanonicalDocument
    docs = [CanonicalDocument.model_validate_json(Path(p).read_text(encoding="utf-8")) for p in paths]
    return {d.document_id: d for d in docs}


def reviewed_originals(model=None, output=None, prices=None):
    """코퍼스 문서의 검토 전 원본 → 현재 규칙의 구조 검토 후보. 원본: 웹 작업의 document.json(검토 후보로 적재된 문서)
    또는 DB 문서(검토 없이 적재된 문서)."""
    from doc_understanding.agents.structure_review.review import review_document
    from doc_understanding.domain.pipeline_contracts import CanonicalDocument
    from doc_understanding.rag import repository
    from doc_understanding.runtime.paths import ROOT
    from doc_understanding.services.corpus_rag import INDEX_VERSION, latest_runs
    jobs = {}
    for path in (ROOT / "work/web-rag").glob("*/stored.json"):
        stored = json.loads(path.read_text(encoding="utf-8"))
        if stored.get("reviewed_document"):
            jobs[stored.get("run_id")] = path.parent.name
    docs = {}
    for run in latest_runs():
        if run["index_version"] != INDEX_VERSION:
            continue
        original = ROOT / "work/document-explorer" / jobs.get(run["run_id"], "-") / "document.json"
        if original.is_file():
            doc = CanonicalDocument.model_validate_json(original.read_bytes())
        elif run["structure_review"]:
            # 이미 검토된 문서를 다시 검토하면 예전 규칙의 결과가 남는다. 원본이 없으면 채점하지 않는다.
            raise SystemExit(f"ORIGINAL_DOCUMENT_UNAVAILABLE run={run['run_id']} ({run['source_name']})")
        else:
            doc = repository.fetch_document(run["run_id"])
        options = {}
        if model:
            # 규칙이 모호로 남긴 표에만 모델(선택 + Judge)을 쓴다. 유료 호출이다.
            from doc_understanding.runtime.structured_call import CallPolicy, Ledger
            options = dict(policy=CallPolicy(model=model, input_usd_per_million=prices[0],
                                             output_usd_per_million=prices[1], max_cost_usd=0.03),
                           ledger=Ledger(max_calls=40, max_total_usd=0.3), allow_external=True,
                           output=output / "model-calls" / doc.document_id.split(":")[-1][:12])
        reviewed, report = review_document(doc, **options)
        print(doc.document_ir.source_name[:24], "applied", len(report["applied"]), "rejected",
              [(r["table_id"][-17:], r["reason"][:40]) for r in report["rejected"]],
              "model calls", len(report["model_calls"]), "cost", round(report["estimated_model_cost_usd"], 4),
              "held", [(d["table_id"][-17:], d["reason"][:32]) for d in report["decisions"]
                       if d["aspect"] == "layout" and d["status"] == "ambiguous"],
              "by model", [d["table_id"][-17:] for d in report["decisions"] if d["source"] == "model"])
        docs[doc.document_id] = reviewed or doc
    return docs


def line(name, s):
    cell = lambda k: "" if s[k]["rate"] is None else f"{s[k]['rate']:.2f} ({s[k]['n']})"  # noqa: E731
    return (f"| {name} | {cell('role_accuracy')} | {cell('answerable')} | {cell('exact')} | {s['wrong_confirmed']} | "
            f"{s['path_right_but_withheld']} | {s['false_value']} | {s['cell_mismatch']} |")


def report_md(data, result, label):
    head = ["| 범위 | 역할 정확도 | 근거 사용 가능 | 경로 정확 | 틀린 확정 | 경로 맞지만 보류 | 값 아닌데 사용 | 좌표 불일치 |",
            "|---|---|---|---|---|---|---|---|"]
    lines = [f"# {data.name} · {label} ({'사람 검토 전 초안' if not data.reviewed_by_human else '검토됨'})", "",
             f"지표 {result['metric_version']}. 누락된 표·셀과 원문 불일치도 전체 분모에 포함한다.", "",
             *head, line("전체", result["summary"]), "", "## 유형별", "", *head]
    lines += [line(p, s) for p, s in result["by_pattern"].items()]
    lines += ["", "## 표별", "", *head]
    lines += [line(f"{t['doc']} {t['table']}", t["summary"]) for t in result["tables"] if "summary" in t]
    s = result["summary"]
    pct = lambda m: "–" if m["rate"] is None else f"{m['rate']:.2f} ({m['n']})"  # noqa: E731
    lines += ["", f"누락 표: {s['missing_tables']}/{s['expected_tables']}, 누락 표의 셀: {s['missing_table_cells']}",
              f"원문 일치: {pct(s['source_match'])}, 원문 일치 대상만의 조건부 경로 정확도: {pct(s['exact_matched'])}"]
    lines += ["", "## 내용을 담는 칸·담긴 표", "",
              f"- 담는 칸 인식: {pct(s['container_found'])}, 안쪽 항목 순서·구성 일치: {pct(s['content_exact'])}",
              f"- 이어 붙인 원문이 값 근거로 쓰임(중복): {s['container_as_value']}",
              f"- 담긴 표의 위치 일치: {pct(s['parent_correct'])}, 근거가 담은 칸 라벨을 함께 실음: {pct(s['context_carried'])}"]
    lines += ["", "## 근거에서 빠진 대상 셀의 원인", ""] + [f"- {k}: {v}" for k, v in result["blockers"].items()]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", type=Path, default=Path("config/evaluation/mapping-dev-v1.json"))
    parser.add_argument("--candidate", nargs="+", type=Path, help="후보 CanonicalDocument JSON 파일들")
    parser.add_argument("--review-originals", action="store_true",
                        help="코퍼스 원본에 현재 규칙의 구조 검토를 다시 적용해 채점(모델 없음)")
    parser.add_argument("--review-model", help="--review-originals에서 모호한 표에 쓸 모델(유료). 단가를 함께 줘야 한다")
    parser.add_argument("--review-input-usd-per-million", type=float)
    parser.add_argument("--review-output-usd-per-million", type=float)
    parser.add_argument("--label", default=None)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    data = load(args.set)
    label = args.label or ("candidate" if args.candidate else "rules" if args.review_originals else "corpus")
    out = args.out or Path("work/mapping-eval") / f"{data.name}-{label}-{datetime.now():%Y%m%d-%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    prices = (args.review_input_usd_per_million, args.review_output_usd_per_million)
    if args.review_model and None in prices:
        raise SystemExit("REVIEW_MODEL_PRICING_REQUIRED: --review-input-usd-per-million, --review-output-usd-per-million")
    docs = (candidate_documents(args.candidate) if args.candidate
            else reviewed_originals(args.review_model, out, prices) if args.review_originals else corpus_documents())
    views = document_views(docs)
    result = score(data, views)
    result["provenance"] = dict(dataset_sha256=digest(data.model_dump()), commit=commit(),
                                documents={key: digest(doc.model_dump(mode="json")) for key, doc in docs.items()})
    failed = mapping_failures.mapping_failures(data, views)
    failure_log.record("mapping", data.name, label, failed, mapping_failures.summarize(failed), out, commit=commit())
    (out / "report.json").write_text(json.dumps(dict(set=data.name, label=label, **result), ensure_ascii=False,
                                                indent=1), encoding="utf-8")
    (out / "report.md").write_text(report_md(data, result, label), encoding="utf-8")
    s = result["summary"]
    print(label, {k: s[k]["rate"] for k in KEYS},
          {k: s[k] for k in ("targets", "wrong_confirmed", "path_right_but_withheld", "false_value", "cell_mismatch")})
    print("containers", {k: s[k]["rate"] for k in ("container_found", "content_exact", "parent_correct", "context_carried")},
          "container_as_value", s["container_as_value"], "nested_tables", s["nested_tables"])
    print("blockers", result["blockers"])
    print("failures", mapping_failures.summarize(failed)["by_kind"], "->", out / "failures.json")
    print("report:", out / "report.md")


if __name__ == "__main__":
    main()
