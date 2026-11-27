"""Offline holdout registration and evaluation; never changes production algorithms."""

# ruff: noqa: E402
import os
import hashlib
import json
import sys
import importlib.metadata
from pathlib import Path
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from doc_understanding.parser.physical.runner import evaluate_pdf
from doc_understanding.domain.models import DocumentIR
from doc_understanding.parser.physical.physical_models import PhysicalTableIR
from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer
from doc_understanding.agents.semantic_resolver.artifacts import save_run
from doc_understanding.agents.semantic_resolver.models import ResolverConfig

OUT = ROOT / "data/namwon-holdout"
SOURCE = Path(
    os.environ.get("DISCLOSURE_SOURCE_DIR", "data/disclosure-source"), "연말결산/2025/남원축협"
)


def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf8")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    config = ResolverConfig(
        max_nodes_per_request=8, max_input_tokens=20000, total_token_budget=50000
    )
    frozen_path = OUT / "frozen-baseline.json"
    paths = sorted(
        p
        for group in ("src", "schemas", "tests/golden", "tests")
        for p in (ROOT / group).rglob("*")
        if p.is_file() and p.suffix in (".py", ".json")
    )
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    if frozen_path.exists():
        assert json.loads(frozen_path.read_text(encoding="utf8"))["files"] == hashes
    else:
        write(
            frozen_path,
            dict(
                version="namwon-holdout-v1",
                files=hashes,
                config=config.model_dump(mode="json"),
                packages={
                    d.metadata["Name"]: d.version for d in importlib.metadata.distributions()
                },
                protocol="source-only selected annotation before inspecting predictions; no tuning; independent human review pending; only annotated edges/path denominators; no live calls",
            ),
        )
    registry = []
    for path in sorted(SOURCE.glob("남원축협_2025.12_*.pdf")):
        if "2025년 외부감사보고서" not in path.name and "별첨" not in path.name:
            continue
        slug = (
            "audit-native"
            if "2025년 외부감사" in path.name
            else "scan-" + path.name.split("별첨")[1][0]
        )
        with pymupdf.open(path) as doc:
            pages = []
            for page in doc:
                text = page.get_text()
                pages.append(
                    dict(
                        page=page.number + 1,
                        native_chars=len("".join(text.split())),
                        native_words=len(page.get_text("words")),
                        drawings=len(page.get_drawings()),
                        images=[dict(width=i[2], height=i[3]) for i in page.get_images()],
                        width=page.rect.width,
                        height=page.rect.height,
                    )
                )
                (OUT / f"{slug}-p{page.number + 1:03d}.txt").write_text(text, encoding="utf8")
            registry.append(
                dict(
                    slug=slug,
                    path=str(path),
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    pages=len(doc),
                    page_inventory=pages,
                    group="native_text_holdout" if slug == "audit-native" else "scan_evaluation",
                    native_chars=sum(p["native_chars"] for p in pages),
                    native_words=sum(p["native_words"] for p in pages),
                )
            )
    assert len(registry) == 7
    write(OUT / "registry.json", registry)
    print([(r["slug"], r["pages"], r["native_chars"]) for r in registry], flush=True)
    manifest_path = OUT / "runs.json"
    records = json.loads(manifest_path.read_text(encoding="utf8")) if manifest_path.exists() else []
    for r in registry:
        if any(e["slug"] == r["slug"] for e in records):
            continue
        run, physical = evaluate_pdf(
            Path(r["path"]), OUT / r["slug"], (), True, label="namwon_unreviewed_holdout"
        )
        document = DocumentIR.model_validate_json((run / "document-ir.json").read_bytes())
        tables = tuple(
            PhysicalTableIR.model_validate_json(p.read_bytes())
            for p in sorted((run / "physical").glob("page-*-table-*.json"))
        )
        structured = tuple(SemanticTableAnalyzer().analyze(t, tables) for t in tables)
        semantic_run, summary = save_run(
            document,
            tables,
            structured,
            OUT / r["slug"] / "semantic",
            mode="rule_only",
            provider=None,
            config=config,
            accounting_scope="unknown",
        )
        records.append(
            dict(
                slug=r["slug"],
                physical_run=str(run),
                semantic_run=str(semantic_run),
                physical=physical,
                semantic=summary,
            )
        )
        write(manifest_path, records)
        print(r["slug"], physical["detected_table_regions"], summary, flush=True)


if __name__ == "__main__":
    main()
