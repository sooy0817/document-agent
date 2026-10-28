import argparse
from pathlib import Path
from doc_understanding.agent_contracts.models import EvidenceBundle
from .runner import execute, export_review, new_run_id


def main():
    parser = argparse.ArgumentParser(description="Offline/mock semantic Agent only")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--review-cases", type=Path)
    args = parser.parse_args()
    evidence = EvidenceBundle.model_validate_json(args.evidence.read_text(encoding="utf8"))
    run = args.output / new_run_id()
    result = execute(evidence, run)
    if args.review_cases and result["status"] == "mock_validated":
        export_review(run, args.review_cases / run.name)
    print(result)


if __name__ == "__main__":
    main()
