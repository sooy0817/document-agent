"""Local corpus registration/extraction only. No external calls or answer generation."""

# ruff: noqa: E402
import os
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from doc_understanding.parser.physical.runner import evaluate_pdf

SOURCE = Path(
    os.environ.get("DISCLOSURE_SOURCE_DIR", "data/disclosure-source"), "연말결산/2025/강구농협"
)
SPECS = [
    ("deficit", "결손금처리계산서(25년)", "development", "unknown"),
    ("business", "사업보고서(25년)", "development", "unknown"),
    ("income-credit", "손익계산서(25년.신용)", "frozen_evaluation", "credit"),
    ("income-general", "손익계산서(25년.일반)", "frozen_evaluation", "general"),
    ("income-integrated", "손익계산서(25년.통합)", "development", "integrated"),
    ("audit", "외부회계감사보고서(25년)", "frozen_evaluation", "unknown"),
    ("capital", "자본변동표(25년)", "development", "unknown"),
    ("balance-credit", "재무상태표(25년.신용)", "frozen_evaluation", "credit"),
    ("balance-general", "재무상태표(25년.일반)", "frozen_evaluation", "general"),
    ("balance-integrated", "재무상태표(25년.통합)", "development", "integrated"),
]


def main():
    out = ROOT / "data/semantic-c1"
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for slug, name, split, scope in SPECS:
        path = SOURCE / f"강구농협_2025.12_{name}.pdf"
        manifest.append(
            dict(
                slug=slug,
                path=str(path),
                accessible=path.is_file(),
                split=split,
                accounting_scope=scope,
                scope_provenance="caller file identity; not inferred from content",
                sha256=hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
            )
        )
    (out / "registry.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf8"
    )
    frozen = [
        "src/doc_understanding/parser/physical/physical_models.py",
        "src/doc_understanding/parser/structure/models.py",
        "schemas/physical-table-ir.schema.json",
        "schemas/structured-table-ir.schema.json",
    ]
    (out / "frozen-contracts.json").write_text(
        json.dumps(
            {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in frozen}, indent=2
        ),
        encoding="utf8",
    )
    for record in manifest:
        if not record["accessible"]:
            continue
        run, summary = evaluate_pdf(Path(record["path"]), out / record["slug"], (), True)
        record.update(run=str(run), physical_summary=summary)
        print(record["slug"], summary["detected_table_regions"], flush=True)
        (out / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf8"
        )


if __name__ == "__main__":
    main()
