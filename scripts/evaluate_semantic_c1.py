"""Separated local corpus modes; real Text LLM must be explicitly invoked via CLI."""

# ruff: noqa: E402
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from doc_understanding.domain.models import DocumentIR
from doc_understanding.parser.physical.physical_models import PhysicalTableIR
from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer
from doc_understanding.agents.semantic_resolver.artifacts import save_run, export_schemas
from doc_understanding.agents.semantic_resolver.models import ResolverConfig
from doc_understanding.agents.semantic_resolver.providers import MockTextAdapter


def main():
    manifest = json.loads((ROOT / "data/semantic-c1/manifest.json").read_text(encoding="utf8"))
    entries = []
    for m in manifest:
        if not m.get("run"):
            continue
        source = Path(m["run"])
        doc = DocumentIR.model_validate_json((source / "document-ir.json").read_bytes())
        tables = tuple(
            PhysicalTableIR.model_validate_json(p.read_bytes())
            for p in sorted((source / "physical").glob("page-*-table-*.json"))
        )
        structured = tuple(SemanticTableAnalyzer().analyze(t, tables) for t in tables)
        entry = dict(slug=m["slug"], split=m["split"], source_run=str(source), runs={})
        for mode in ["rule_only", "llm_only", "hybrid"]:
            run, summary = save_run(
                doc,
                tables,
                structured,
                ROOT / "data/semantic-c1-results" / m["slug"] / mode,
                mode=mode,
                provider=None if mode == "rule_only" else MockTextAdapter(),
                config=ResolverConfig(
                    max_nodes_per_request=8, max_input_tokens=20000, total_token_budget=50000
                ),
                accounting_scope=m["accounting_scope"],
            )
            entry["runs"][mode] = dict(path=str(run), summary=summary)
        entries.append(entry)
        (ROOT / "reports/phase2c1-runs.json").write_text(
            json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf8"
        )
        print(m["slug"], entry["runs"]["rule_only"]["summary"]["decisions"], flush=True)
    export_schemas(ROOT / "schemas")


if __name__ == "__main__":
    main()
