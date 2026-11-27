"""Check the original C1 hashes without rewriting historical contracts or sources.

ADR-039/042/075 extend only analyzer_version's accepted literals. The authorized
layout move adds a role comment and changes one import. Invert exactly those
edits, then require the original SHA256; schemas remain byte-exact.
"""
import hashlib
import json
from pathlib import Path

OLD_PHYSICAL = 'src/doc_understanding/table_understanding/physical_models.py'
OLD_SEMANTIC = 'src/doc_understanding/table_understanding/semantic_models.py'
MOVED = {
    OLD_PHYSICAL: 'src/doc_understanding/parser/physical/physical_models.py',
    OLD_SEMANTIC: 'src/doc_understanding/parser/structure/models.py',
}
BASE_VERSION = '    analyzer_version: Literal["semantic-geometry/0.1.0"] = "semantic-geometry/0.1.0"'
VERSIONS = ('0.1.0', '0.2.0', '0.3.0', '0.3.1')


def matches_original(raw, expected, original_path, *, relocated=False):
    if hashlib.sha256(raw).hexdigest() == expected:
        return True
    if original_path not in MOVED:
        return False
    text = raw.decode('utf8').replace('\r\n', '\n')
    if relocated:
        lines = text.splitlines(keepends=True)
        if lines and lines[0].startswith('# 역할: '):
            text = ''.join(lines[1:])
        if original_path == OLD_SEMANTIC:
            text = text.replace(
                'from doc_understanding.parser.physical.physical_models import Boundary, PhysicalTableIR\n',
                'from .physical_models import Boundary, PhysicalTableIR\n',
            )
    if original_path == OLD_SEMANTIC:
        # Exact prefixes only: unknown, removed, reordered versions still fail.
        for count in range(2, len(VERSIONS) + 1):
            literals = ', '.join('"semantic-geometry/' + v + '"' for v in VERSIONS[:count])
            approved = '    analyzer_version: Literal[' + literals + '] = "semantic-geometry/0.1.0"'
            text = text.replace(approved + '\n', BASE_VERSION + '\n')
    # The original physical file used CRLF; the semantic fixture used LF.
    return any(hashlib.sha256(candidate.encode('utf8')).hexdigest() == expected
               for candidate in (text, text.replace('\n', '\r\n')))


def check(root):
    snapshot = json.loads((root / 'data/semantic-c1/frozen-contracts.json').read_text(encoding='utf8'))
    failures = []
    for name, expected in snapshot.items():
        moved = MOVED.get(name)
        relocated = bool(moved and (root / moved).is_file())
        target = root / (moved if relocated else name)
        # Both layouts present is ambiguous and can hide a stale original file.
        if relocated and (root / name).exists():
            failures.append(name)
            continue
        try:
            valid = matches_original(target.read_bytes(), expected, name, relocated=relocated)
        except (OSError, UnicodeError):
            valid = False
        if not valid:
            failures.append(name)
    return {'checked': len(snapshot), 'changed_or_missing': failures}


if __name__ == '__main__':
    result = check(Path(__file__).resolve().parents[1])
    print(json.dumps(result))
    raise SystemExit(bool(result['changed_or_missing']))
