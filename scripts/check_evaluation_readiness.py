"""평가셋 범위와 저장된 적재 결과를 오프라인 점검한다. DB·파서·모델은 실행하지 않는다."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from doc_understanding.evaluation.mapping.dataset import load as load_mapping
from doc_understanding.evaluation.rag.dataset import load as load_rag
from doc_understanding.evaluation.rag.metrics import latency, summarize


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def datasets(folder):
    sets, docs = [], {}
    for path in sorted(Path(folder).glob("*.json")):
        raw = read(path)
        if not ("tables" in raw or "items" in raw):
            continue
        mapping = "tables" in raw
        data = load_mapping(path) if mapping else load_rag(path)
        docs[data.name] = set(data.documents.values())
        entry = dict(name=data.name, sha256=sha(path), status=data.status,
                     reviewed_by_human=data.reviewed_by_human, documents=len(docs[data.name]), note=data.note)
        if mapping:
            cells = [c for t in data.tables for c in t.cells]
            entry.update(tables=len(data.tables), cells=len(cells),
                         human_reviewed_cells=sum(c.reviewed and c.reviewer == "human" for c in cells),
                         roles=dict(Counter(c.role for c in cells)),
                         patterns=dict(Counter(p for t in data.tables for p in set(t.patterns))))
        else:
            entry.update(questions=len(data.items), types=dict(Counter(i.type for i in data.items)),
                         multi_anchor_questions=sum(len(i.gold) > 1 for i in data.items),
                         unanswerable=sum(not i.answerable for i in data.items),
                         ambiguous=sum(i.ambiguous for i in data.items))
        sets.append(entry)
    overlaps = [dict(a=a, b=b, documents=len(docs[a] & docs[b])) for a in docs for b in docs
                if a < b and docs[a] & docs[b]]
    return dict(sets=sets, unique_documents=len(set().union(*docs.values())), overlaps=overlaps,
                independent_holdout_verified=False, semantic_accuracy_verified=False)


def batch(folder):
    folder = Path(folder)
    summary, jobs = read(folder / "summary.json"), read(folder / "jobs.json")
    done = [j for j in jobs if j["status"] == "done"]
    payloads, missing = {}, []
    for j in done:
        path = Path(j["result"]["output"]) / "result.json"
        if path.is_file():
            # 전체 표·셀 원문·좌표·병합·경로·문맥·페이지를 비교한다. 요약 개수만 비교하지 않는다.
            payloads[j["document_sha256"]] = hashlib.sha256(
                json.dumps(read(path), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        else:
            missing.append(j["document_sha256"])
    return dict(name=folder.name, report_sha256=sha(folder / "summary.json"), jobs_sha256=sha(folder / "jobs.json"),
                documents=len(jobs), work_wall_seconds=summary.get("work_wall_seconds"),
                statuses=dict(Counter(j["status"] for j in jobs)),
                latency_seconds=latency([j["result"]["seconds"] for j in done]),
                peak_worker_rss_mb=max((j["result"].get("peak_rss_mb") or 0 for j in done), default=None),
                host_peak_memory_measured=False, concurrent_query_latency_measured=False,
                missing_result_files=missing, duplicate_inputs=len(jobs) - len({j["document_sha256"] for j in jobs}),
                outcomes={j["document_sha256"]: j["status"] for j in jobs}, result_hashes=payloads,
                table_states=summary.get("table_states"), page_text_status=summary.get("page_text_status"))


def compare(a, b):
    shared = set(a["result_hashes"]) & set(b["result_hashes"])
    return dict(a=a["name"], b=b["name"], same_inputs=set(a["outcomes"]) == set(b["outcomes"]),
                same_outcomes=a["outcomes"] == b["outcomes"], compared_documents=len(shared),
                changed_results=[key for key in sorted(shared) if a["result_hashes"][key] != b["result_hashes"][key]],
                complete_result_coverage=bool(shared) and not (a["missing_result_files"] or b["missing_result_files"]
                                                              or a["duplicate_inputs"] or b["duplicate_inputs"])
                    and set(a["result_hashes"]) == set(b["result_hashes"]))


def legacy_rag(path):
    report = read(path)
    configs = {}
    for name, block in report["configs"].items():
        original = block.get("summary", {})
        summary = summarize(block["items"])
        # 저장된 판정을 다시 집계할 때 새 비교 근거 규칙을 적용했다고 표시하지 않는다.
        summary["metric_version"] = original.get("metric_version", report.get("metric_version"))
        summary["answer_scoring"] = original.get("answer_scoring")
        configs[name] = summary
    return dict(path=str(path), sha256=sha(path), reaggregated_only=True,
                original_reference_snapshot_available=(Path(path).parent / "evaluation-snapshot.json").is_file(),
                configs=configs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sets", type=Path, default=Path("config/evaluation"))
    parser.add_argument("--batch", type=Path, nargs="*", default=[])
    parser.add_argument("--rag", type=Path, nargs="*", default=[], help="옛 점수의 시간·비용을 재집계(정답 재채점 아님)")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = dict(datasets=datasets(args.sets), batches=[batch(p) for p in args.batch],
                  legacy_rag=[legacy_rag(p) for p in args.rag])
    report["comparisons"] = [compare(report["batches"][0], b) for b in report["batches"][1:]]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(dict(unique_documents=report["datasets"]["unique_documents"],
                         comparisons=report["comparisons"], report=str(args.out)), ensure_ascii=False))


if __name__ == "__main__":
    main()
