# 역할: 같은 정답·분모·지표 버전의 파서 실행 점수를 비교하고 불변 기록으로 남긴다.
"""Compare already-evaluated runs against the same frozen reference set.

This module never turns predictions into a reference or approves parser changes.
"""
import math
from datetime import datetime, timezone
from pathlib import Path

from doc_understanding.evaluation.ledger import digest, encoded, put

# 지표 이름은 같은 폴더의 golden.py, tree_accuracy.py와 공유한다.
METRICS = {
    "detection_precision": "higher", "detection_recall": "higher",
    "cell_geometry_accuracy": "higher", "merged_span_accuracy": "higher",
    "exact_row_path": "higher", "column_path": "higher", "cell_to_path": "higher",
}


def compare_runs(before, after):
    for key in ("dataset_sha256", "reference_sha256", "metric_version"):
        if not before.get(key) or before.get(key) != after.get(key):
            raise ValueError("INCOMPARABLE_" + key.upper())
    if not all(x.get("reference_status") == "human_confirmed" for x in (before, after)):
        raise ValueError("REFERENCE_NOT_HUMAN_CONFIRMED")
    if not all(x.get("parser_fingerprint") for x in (before, after)):
        raise ValueError("PARSER_FINGERPRINT_MISSING")
    if set(before.get("metrics", {})) != set(after.get("metrics", {})):
        raise ValueError("METRIC_SET_MISMATCH")
    comparisons = {}
    for name, b in before.get("metrics", {}).items():
        a = after["metrics"][name]
        if name not in METRICS or not isinstance(b, dict) or not isinstance(a, dict):
            raise ValueError("INVALID_METRIC")
        if b.get("total") != a.get("total") or not isinstance(b.get("total"), int) or b["total"] < 0:
            raise ValueError("DENOMINATOR_MISMATCH")
        total = b["total"]
        for item in (a, b):
            n = item.get("correct")
            if not isinstance(n, int) or not 0 <= n <= total or not math.isfinite(n):
                raise ValueError("INVALID_METRIC_COUNT")
        if not total:
            comparisons[name] = {"before": None, "after": None, "delta": None, "status": "not_measurable", "total": 0}
            continue
        delta = (a["correct"] - b["correct"]) / total
        comparisons[name] = {"before": b["correct"] / total, "after": a["correct"] / total,
                             "delta": delta, "status": "regression" if delta < 0 else "improved" if delta > 0 else "unchanged",
                             "before_correct": b["correct"], "after_correct": a["correct"], "total": total}
    if not comparisons:
        raise ValueError("NO_METRICS")
    return {"schema_version": "campaign-comparison/1", "dataset_sha256": before["dataset_sha256"],
            "reference_sha256": before["reference_sha256"], "metric_version": before["metric_version"],
            "before_parser": before["parser_fingerprint"], "after_parser": after["parser_fingerprint"],
            "metrics": comparisons, "human_approval": False, "parser_applied": False}


def record_comparison(folder, before, after):
    report = compare_runs(before, after)
    # Identity includes both original inputs, and records are append-only.
    body = {"before": before, "after": after, "comparison": report}
    ident = digest(encoded(body))
    path = Path(folder) / "comparisons" / (ident + ".json")
    if not path.exists():
        put(path, encoded({"id": ident, "recorded_at": datetime.now(timezone.utc).isoformat(), **body}))
    return path
