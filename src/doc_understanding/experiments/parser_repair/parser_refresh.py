# 역할: 파서 수정 제안·격리 실행·재추출 큐를 관리한다. 정답 비교는 evaluation.parser에 위임한다.
"""Offline saved-output/current-code comparison before spending on a new patch."""

import shutil
from pathlib import Path

from doc_understanding.review.presentation.metrics import page_metrics
from doc_understanding.evaluation.parser.snapshots import delta

from doc_understanding.experiments.parser_repair import engine as repair


def find_saved(data, pdf_hash):
    # Prefer the oldest stored run; comparing against this experiment's own preview is misleading.
    matches = []
    for folder in Path(data).iterdir():
        if len(folder.name) != 32 or not all(c in "0123456789abcdef" for c in folder.name):
            continue
        if all(
            (folder / name).is_file()
            for name in ("input.pdf", "document.json", "nested.json", "result.json")
        ):
            if repair.sha(folder / "input.pdf") == pdf_hash:
                matches.append(folder)
    return min(matches, key=lambda p: (p / "document.json").stat().st_mtime) if matches else None


def execute(record, data, folder, progress, *, runner=repair.container):
    snap = record["snapshot"]
    saved = Path(data) / snap["job_id"]
    if repair.sha(saved / "input.pdf") != snap["source_pdf_sha256"]:
        raise ValueError("SOURCE_HASH_MISMATCH")
    if repair.sha(saved / "document.json") != snap["canonical_sha256"]:
        raise ValueError("SAVED_OUTPUT_CHANGED")
    progress("freeze", "의견·과거 JSON·최신 코드 버전을 고정합니다. 모델 호출은 없습니다.")
    hashes = repair.freeze(folder / "current-code")
    repair.save(folder / "code-manifest.json", hashes)
    repair.save(folder / "request.json", record)
    repair.save(
        folder / "usage.json", dict(mode="local_refresh", api_calls=0, estimated_cost_usd=0)
    )
    registry = Path(data) / "parser-repair-datasets.json"
    datasets = [
        dict(
            label=snap.get("document_name", "선택 문서"),
            role="development",
            path=str(saved / "input.pdf"),
            sha256=snap["source_pdf_sha256"],
            saved_job=str(saved),
        )
    ]
    seen = {snap["source_pdf_sha256"]}
    for entry in repair.read(registry) if registry.exists() else []:
        pdf = repair.ROOT / entry["path"]
        if (
            entry["role"] not in ("regression", "evaluation", "holdout")
            or not pdf.is_file()
            or repair.sha(pdf) != entry["sha256"]
        ):
            raise ValueError("DATASET_MANIFEST_INVALID")
        if entry["sha256"] in seen:
            continue
        seen.add(entry["sha256"])
        old = find_saved(data, entry["sha256"])
        datasets.append({**entry, "path": str(pdf), "saved_job": str(old) if old else None})
        if len(datasets) >= 6:
            break
    repair.save(folder / "datasets.json", datasets)
    rows = []
    for index, entry in enumerate(datasets):
        progress("reparse", entry["label"] + " · 과거 저장본과 최신 파서 실행을 비교합니다.")
        output = folder / "outputs" / str(index)
        before, after = output / "baseline", output / "candidate"
        before.mkdir(parents=True)
        if entry["saved_job"]:
            for name in ("document.json", "nested.json", "result.json"):
                shutil.copy2(Path(entry["saved_job"]) / name, before / name)
        code = runner(
            folder / "current-code",
            after,
            ["python", "-B", "-m", "doc_understanding.services.documents", "/input.pdf", "/output"],
            Path(entry["path"]),
        )
        comparison = None
        if code == 0 and (after / "document.json").exists() and (before / "document.json").exists():
            docs = [repair.read(p / "document.json") for p in (before, after)]
            nested = [repair.read(p / "nested.json") for p in (before, after)]
            comparison = delta(*docs)
            comparison["pages"] = [
                dict(before=a, after=b)
                for a, b in zip(page_metrics(docs[0], nested[0]), page_metrics(docs[1], nested[1]))
            ]
            comparison["nested_context_changed"] = any(
                p["before"] != p["after"] for p in comparison["pages"]
            )
        rows.append(
            dict(
                dataset=entry,
                outputs=dict(
                    baseline=dict(path=str(before)), candidate=dict(path=str(after), exit_code=code)
                ),
                comparison=comparison,
                comparison_missing_reason=None
                if comparison
                else "NO_SAVED_BASELINE_OR_EXTRACTION_FAILED",
            )
        )
        repair.save(folder / "results.json", rows)
    failed = any(
        r["outputs"]["candidate"]["exit_code"] != 0
        or (r["comparison"] and not r["comparison"]["native_text_preserved"])
        for r in rows
    )
    result = dict(
        mode="local_refresh",
        status="validation_failed" if failed else "awaiting_source_review",
        diagnosis="과거 저장 결과와 최신 파서 비교 · 새 코드 수정 없이 재추출",
        limits="과거 실행 코드 해시는 알 수 없습니다. 이번 결과는 기존 개선의 재반영이며 새 코드 수정 효과나 의미 정답률이 아닙니다. 보류·누락은 원본 검수가 필요합니다.",
        datasets=rows,
        tests=dict(baseline=None, candidate=None),
        changed_files=[],
        candidate_modified=False,
        stable_parser_modified=False,
        semantic_accuracy_verified=False,
        generalization_verified=False,
        holdout_count=sum(d["role"] == "holdout" for d in datasets),
        usage=dict(api_calls=0, estimated_cost_usd=0),
        stable_code_unchanged=all(
            (repair.ROOT / n).is_file() and repair.sha(repair.ROOT / n) == h
            for n, h in hashes.items()
            if n.startswith("src/")
        ),
    )
    repair.save(folder / "result.json", result)
    progress(
        "report",
        "문서·페이지별 전후 결과를 저장했습니다. 남은 오류를 확인한 뒤 코드 수정 실험으로 넘길 수 있습니다.",
    )
    return result
