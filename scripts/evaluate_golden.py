import argparse
import hashlib
import json
from pathlib import Path

from doc_understanding.evaluation.parser.golden import evaluate

parser = argparse.ArgumentParser()
parser.add_argument("folder", type=Path)
parser.add_argument("--golden", type=Path, default=Path("tests/golden/ganggu-physical-v1.json"))
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
gold = json.loads(args.golden.read_text(encoding="utf-8"))
source_ir = json.loads((args.folder.parent/'document-ir.json').read_text(encoding='utf-8'))
if source_ir['source_sha256'] != gold['source_sha256']:
    raise ValueError('Golden and evaluated source PDF hashes differ')
result = evaluate(args.folder, gold)
result["golden_sha256"] = hashlib.sha256(args.golden.read_bytes()).hexdigest()
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(
    json.dumps(
        {k: v for k, v in result.items() if k not in ("table_details", "false_positive_regions")},
        ensure_ascii=True,
    )
)
