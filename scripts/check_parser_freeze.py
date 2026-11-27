"""Run the same frozen-parser guard used by review campaigns."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from doc_understanding.runtime.parser_freeze import check, equivalent_source


def main():
    result = check(ROOT)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result['changed_or_missing'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
