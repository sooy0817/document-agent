import argparse
import sys
from pathlib import Path

from .config import artifact_root, extraction_limits
from .domain.contracts import ExtractionError, ExtractionRequest
from .domain.models import DocumentIR, DocumentMetadata
from .orchestrator.workflow import extract_ir


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Phase 1 PDF evidence extraction, not canonical facts")
    commands = parser.add_subparsers(dest="command", required=True)
    extract = commands.add_parser("extract-ir")
    extract.add_argument("--pdf", type=Path, required=True)
    extract.add_argument("--output-dir", type=Path, default=artifact_root())
    extract.add_argument("--institution")
    extract.add_argument("--reporting-period")
    schema = commands.add_parser("schema")
    schema.add_argument("--output", type=Path, required=True)
    grid = commands.add_parser("physical-grid")
    grid.add_argument("--pdf",type=Path,required=True)
    grid.add_argument("--output-dir",type=Path,default=artifact_root())
    grid.add_argument("--focus-pages",default="8,14,15,18")
    grid.add_argument("--focus-only",action="store_true")
    grid.add_argument("--mode",choices=["auto","vector","raster","mixed"],default="auto")
    grid.add_argument("--boundary-profile",choices=["stroke","stroke_fill"],default="stroke_fill")
    grid.add_argument("--no-nested-regions",action="store_true")
    semantic = commands.add_parser("semantic-structure", help="Generic PhysicalTableIR 2.1 to StructuredTableIR 2.0")
    source = semantic.add_mutually_exclusive_group(required=True)
    source.add_argument("--physical-dir", type=Path)
    source.add_argument("--physical-ir", type=Path)
    source.add_argument("--pdf", type=Path)
    semantic.add_argument("--source-pdf", type=Path, help="Optional local PDF for overlays of existing physical IR")
    semantic.add_argument("--pages", default="", help="Comma-separated 1-based PDF pages; default all")
    semantic.add_argument("--output-dir", type=Path, default=artifact_root() / "semantic")
    resolver = commands.add_parser("semantic-resolve", help="Phase 2-C1 immutable text semantic overlay")
    resolver_source = resolver.add_mutually_exclusive_group(required=True)
    resolver_source.add_argument("--input-run", type=Path, help="Existing run with document-ir.json and physical/")
    resolver_source.add_argument("--pdf", type=Path)
    resolver.add_argument("--mode", choices=["rule_only", "llm_only", "hybrid"], default="rule_only")
    resolver.add_argument("--provider", choices=["mock", "openai"], default="mock")
    resolver.add_argument("--config", type=Path)
    resolver.add_argument("--allow-external", action="store_true")
    resolver.add_argument("--prepare-only", action="store_true", help="Write bounded text requests without calls")
    resolver.add_argument("--accounting-scope", choices=["unknown", "credit", "general", "integrated"], default="unknown")
    resolver.add_argument("--output-dir", type=Path, default=artifact_root() / "semantic-resolver")
    args = parser.parse_args(argv)
    try:
        if args.command == "semantic-resolve":
            from .table_understanding.physical_models import PhysicalTableIR
            from .table_understanding.semantic import SemanticTableAnalyzer
            from .semantic_resolver.models import ResolverConfig
            from .semantic_resolver.providers import MockTextAdapter, OpenAITextAdapter
            from .semantic_resolver.artifacts import save_run
            config = ResolverConfig.model_validate_json(args.config.read_bytes()) if args.config else ResolverConfig()
            config = config.model_copy(update={"allow_external": args.allow_external})
            if args.provider == "openai" and not args.prepare_only and args.mode != "rule_only":
                if not args.allow_external or not config.model_id:
                    parser.error("Live calls require --allow-external and an explicit configured model_id")
            source_run = args.input_run
            if args.pdf:
                from .eval.physical import evaluate_pdf
                source_run, _ = evaluate_pdf(args.pdf, args.output_dir / "inputs", (), True)
            doc = DocumentIR.model_validate_json((source_run / "document-ir.json").read_bytes())
            tables = tuple(PhysicalTableIR.model_validate_json(f.read_bytes()) for f in sorted((source_run / "physical").glob("page-*-table-*.json")))
            structures = tuple(SemanticTableAnalyzer().analyze(t, tables) for t in tables)
            provider = None if args.prepare_only or args.mode == "rule_only" else OpenAITextAdapter() if args.provider == "openai" else MockTextAdapter()
            run, summary = save_run(doc, tables, structures, args.output_dir, mode=args.mode,
                                    provider=provider, config=config, accounting_scope=args.accounting_scope)
            print(run / "index.html")
            print(f"status={summary['status']}; calls={len(summary['calls'])}; independent_review=pending")
            return 0
        if args.command == "semantic-structure":
            from .eval.semantic import write_evaluation
            pages = tuple(int(p) for p in args.pages.split(",") if p)
            if args.pdf:
                from .eval.physical import evaluate_pdf
                physical_run, _ = evaluate_pdf(args.pdf, args.output_dir / "physical-input", pages, not bool(pages))
                files = sorted((physical_run / "physical").glob("page-*-table-*.json"))
            elif args.physical_ir:
                files = [args.physical_ir]
            else:
                files = sorted(args.physical_dir.glob("page-*-table-*.json"))
            if not files:
                parser.error("No PhysicalTableIR input files found")
            run, summary = write_evaluation(files, args.output_dir, args.pdf or args.source_pdf, pages)
            print(run / "index.html")
            print(f"tables={summary['table_count']}; human_review=pending; llm_calls=0")
            return 0
        if args.command == "schema":
            import json
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(DocumentIR.model_json_schema(), indent=2), encoding="utf-8")
            return 0
        if args.command == "physical-grid":
            from .eval.physical import evaluate_pdf
            focus = tuple(int(p) for p in args.focus_pages.split(",") if p)
            run,summary = evaluate_pdf(args.pdf,args.output_dir,focus,not args.focus_only,args.mode,
                                      boundary_profile=args.boundary_profile,nested_regions=not args.no_nested_regions)
            print(run / "physical/summary.json")
            print(f"regions={summary['detected_table_regions']}; automatic_pass={summary['automatic_structure_pass']}; accuracy_proven=False")
            return 0
        from .extraction.pymupdf_adapter import PyMuPDFExtractor
        limits = extraction_limits()
        with args.pdf.open("rb") as stream:
            data = stream.read(limits.max_bytes + 1)
        metadata = DocumentMetadata(institution=args.institution, reporting_period=args.reporting_period,
                                    provenance="caller_supplied" if args.institution or args.reporting_period else "unknown")
        ir = extract_ir(ExtractionRequest(data, args.pdf.name, metadata, limits),
                        PyMuPDFExtractor(), args.output_dir)
        print(args.output_dir / str(ir.run.run_id) / "document-ir.json")
        print(f"{ir.processing_status}; validation={ir.validation_status}; pages={len(ir.pages)}")
        return 0
    except ImportError as exc:
        print(f"Missing dependency: {exc.name}; install the project dependencies.", file=sys.stderr)
        return 2
    except ExtractionError as exc:
        print(exc.code, file=sys.stderr)
        return 2
    except OSError:
        print("LOCAL_IO_ERROR", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
