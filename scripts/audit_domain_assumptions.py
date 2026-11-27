"""Read-only inventory of literal assumptions; findings require human/code review.

Scans root runtime, scripts, tests and opt-in config. It does not open private
documents, run experiments, access networks, or scan separate release repositories.
This is an inventory aid, not a proof that every semantic assumption was found.
"""
import argparse
import ast
import hashlib
import json
import re
from pathlib import Path


PATTERNS = {
    'named_domain': r'농협|축협|남평|남원|강구|강동|N03|N08|krihs|bok\.or|하나은행|KB퇴직|LH 광명',
    'historical_artifact': r'(?:data|reports|outputs)[/\\][^\n\x27\x22]*(?:20\d{6}|architecture-v1|phase3a|semantic-c1|namwon|ganggu|frozen-e2e|semantic-human-review|semantic-priority-review)',
    'machine_path': r'[A-Za-z]:[/\\](?:Users|workspace|root)[/\\]|/home/[^/]+/',
    'model_binding': r'gpt-4\.1|claude-[A-Za-z0-9-]+|DEMO_MODEL_',
    'legacy_domain_contract': r'accounting_scope|deposit_status|metric_mapping',
}


def audit(root):
    root = Path(root).resolve()
    freeze = json.loads((root/'reports/parser-freeze-20261105/manifest.json').read_text(encoding='utf8'))
    protected = set(freeze['protected_hashes'])
    # Follow the separately maintained relocation map without executing checker code.
    checker = ast.parse((root/'scripts/check_parser_freeze.py').read_text(encoding='utf-8-sig'))
    relocations = next((ast.literal_eval(node.value) for node in checker.body
                        if isinstance(node, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == 'RELOCATIONS'
                                for t in node.targets)), {})
    protected.update('src/' + relocations[module].replace('.', '/') + '.py'
                     for name in freeze['protected_hashes']
                     if (module := name.removeprefix('src/').removesuffix('.py').replace('/', '.'))
                     in relocations)
    records = []
    for folder in ('src', 'scripts', 'tests', 'config'):
        for path in sorted((root/folder).rglob('*')):
            if not path.is_file() or path.suffix not in ('.py', '.ps1', '.js', '.html', '.json', '.sql'):
                continue
            relative = path.relative_to(root).as_posix()
            if '__pycache__' in path.parts or path.name == Path(__file__).name:
                continue
            raw = path.read_bytes()
            lines = raw.decode('utf-8-sig').splitlines()
            findings = [{ 'line': i, 'kind': kind }
                        for i, line in enumerate(lines, 1)
                        for kind, pattern in PATTERNS.items() if re.search(pattern, line, re.I)]
            category = ('protected_parser' if relative in protected else
                        'regression_fixture' if folder == 'tests' else
                        'opt_in_example' if folder == 'config' else
                        'explicit_tool' if folder == 'scripts' else 'runtime')
            records.append(dict(path=relative, category=category,
                                sha256=hashlib.sha256(raw).hexdigest(), findings=findings))
    return dict(schema_version='domain-assumption-audit/1', files_scanned=len(records),
                files_with_findings=sum(bool(r['findings']) for r in records), files=records,
                caveat='Pattern inventory plus manual review; no completeness/accuracy certification',
                excluded=['.agent historical instructions', 'private data and frozen reports',
                          'github-portfolio separate repository', 'public-release snapshot'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = audit(args.root)
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf8') as stream:
            stream.write(encoded+'\n')
        print(json.dumps({k: result[k] for k in ('files_scanned', 'files_with_findings')}))
    else:
        print(encoded)


if __name__ == '__main__':
    main()
