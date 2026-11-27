"""Generate actual PDF/PNG/JSON outputs for synthetic golden evaluation."""

# ruff: noqa: E402 -- local source bootstrap for running without editable install
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from doc_understanding.parser.physical.runner import evaluate_pdf
from tests.physical_fixtures import fixture_pdf


def main():
    folder = ROOT / "reports/physical-fixtures"
    folder.mkdir(parents=True, exist_ok=True)
    results = []
    for kind, scan in [
        ("regular", False),
        ("horizontal", False),
        ("vertical", False),
        ("partial_horizontal", False),
        ("nested", False),
        ("parallel", False),
        ("incomplete", False),
        ("borderless", False),
        ("vertical", True),
        ("nested", True),
    ]:
        name = kind + ("-scan" if scan else "")
        path = folder / f"{name}.pdf"
        data, golden = fixture_pdf(kind, scan=scan)
        path.write_bytes(data)
        run, summary = evaluate_pdf(
            path, folder / "runs", focus_pages=(1,), label="synthetic_fixture"
        )
        expected = [
            {
                "row_index": c.row,
                "column_index": c.column,
                "rowspan": c.rowspan,
                "colspan": c.colspan,
                "label": c.label,
            }
            for c in golden[2]
        ]
        (folder / f"{name}-golden.json").write_text(
            json.dumps(expected, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        results.append({"fixture": name, "run": str(run.relative_to(ROOT)), "summary": summary})
    (folder / "evaluation.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps([{k: v for k, v in r.items() if k != "summary"} for r in results], indent=2))


if __name__ == "__main__":
    main()
