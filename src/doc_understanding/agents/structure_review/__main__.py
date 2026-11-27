# 역할: 구조 검토 CLI. 기존 문서를 읽어 새 폴더에 검토 후보 문서와 보고서를 쓴다. 기존 파일은 덮어쓰지 않는다.
import argparse
import json
from pathlib import Path

from doc_understanding.domain.pipeline_contracts import CanonicalDocument


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m doc_understanding.agents.structure_review")
    parser.add_argument("--document", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new directory")
    parser.add_argument("--allow-external", action="store_true",
                        help="send held tables to the model (rule-only by default)")
    parser.add_argument("--model", default="gpt-4.1")
    parser.add_argument("--input-usd-per-million", type=float)
    parser.add_argument("--output-usd-per-million", type=float)
    parser.add_argument("--max-cost-usd", type=float, default=0.05, help="per call")
    parser.add_argument("--max-total-usd", type=float, default=0.5)
    parser.add_argument("--max-calls", type=int, default=20)
    args = parser.parse_args(argv)
    from doc_understanding.agents.structure_review.review import review_document
    from doc_understanding.runtime.structured_call import CallPolicy, Ledger

    args.output.mkdir(parents=True, exist_ok=False)
    doc = CanonicalDocument.model_validate_json(args.document.read_bytes())
    policy = ledger = None
    if args.allow_external:
        if args.input_usd_per_million is None or args.output_usd_per_million is None:
            raise SystemExit("EXPLICIT_MODEL_PRICING_REQUIRED")
        policy = CallPolicy(model=args.model, input_usd_per_million=args.input_usd_per_million,
                            output_usd_per_million=args.output_usd_per_million, max_cost_usd=args.max_cost_usd)
        ledger = Ledger(max_calls=args.max_calls, max_total_usd=args.max_total_usd)
    reviewed, report = review_document(doc, policy=policy, allow_external=args.allow_external,
                                       output=args.output / "model-calls", ledger=ledger)
    report["source_document"] = str(args.document)
    if reviewed is not None:
        with (args.output / "reviewed-document.json").open("x", encoding="utf-8") as stream:
            stream.write(reviewed.model_dump_json(indent=2))
    with (args.output / "structure-review.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(dict(report["summary"], applied=len(report["applied"]),
                          rejected=len(report["rejected"]), reviewed_document=reviewed is not None)))


if __name__ == "__main__":
    main()
