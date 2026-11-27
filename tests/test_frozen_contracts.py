"""Fast contract checks: no PDF extraction, server, corpus fixture or model call."""
import hashlib
from pathlib import Path

import pytest

from scripts.check_semantic_contracts import (
    BASE_VERSION, OLD_PHYSICAL, OLD_SEMANTIC, check, matches_original,
)


def test_frozen_upstream_contracts():
    assert check(Path(__file__).resolve().parents[1]) == {'checked': 4, 'changed_or_missing': []}


ORIGINAL = ('from .physical_models import Boundary, PhysicalTableIR\n'
            'class Model:\n' + BASE_VERSION + '\n    limit: int = 12\n')
DIGEST = hashlib.sha256(ORIGINAL.encode()).hexdigest()
CURRENT = ORIGINAL.replace('"semantic-geometry/0.1.0"]',
    '"semantic-geometry/0.1.0", "semantic-geometry/0.2.0", "semantic-geometry/0.3.0", "semantic-geometry/0.3.1"]')
MOVED = '# 역할: 계약 모델\n' + CURRENT.replace('from .physical_models',
    'from doc_understanding.parser.physical.physical_models')


@pytest.mark.parametrize('source,relocated', [(CURRENT, False), (MOVED, True), (MOVED.replace('\n', '\r\n'), True)])
def test_documented_version_and_layout_edits_only(source, relocated):
    assert matches_original(source.encode(), DIGEST, OLD_SEMANTIC, relocated=relocated)


@pytest.mark.parametrize('source', [
    MOVED.replace('limit: int = 12', 'limit: int = 11'),
    MOVED.replace('limit: int', 'limit: str'),
    MOVED.replace('0.3.1', '9.9.9'),
    MOVED.replace(', "semantic-geometry/0.2.0"', ''),
    MOVED.replace('= "semantic-geometry/0.1.0"', '= "semantic-geometry/0.3.1"'),
    MOVED.replace('import Boundary, PhysicalTableIR', 'import Boundary, OtherIR'),
    MOVED + 'print("unexpected execution")\n',
])
def test_unapproved_contract_edits_fail(source):
    assert not matches_original(source.encode(), DIGEST, OLD_SEMANTIC, relocated=True)


def test_schema_bytes_and_physical_constants_remain_strict():
    raw = b'{"limit": 12}\n'
    digest = hashlib.sha256(raw).hexdigest()
    assert not matches_original(raw.replace(b'12', b'11'), digest, OLD_PHYSICAL)
    assert not matches_original(raw.replace(b'\n', b'\r\n'), digest, 'schemas/physical-table-ir.schema.json')
