# 역할: 폴더 이동을 허용해도 동결된 알고리즘·상수·KB 결과 변경은 계속 차단되는지 확인한다.
import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from scripts.check_parser_freeze import check, equivalent_source


OLD = 'src/doc_understanding/table_understanding/physical_grid.py'
NEW = 'src/doc_understanding/parser/physical/physical_grid.py'
BEFORE = b'from .physical_models import GridPolicy\nLIMIT = 12\n'
AFTER = '# 역할: 이동한 물리 파서\nfrom doc_understanding.parser.physical.physical_models import GridPolicy\nLIMIT = 12\n'.encode('utf8')


def test_only_comments_and_authorized_import_relocation_are_equivalent():
    assert equivalent_source(BEFORE, AFTER, OLD, NEW)


@pytest.mark.parametrize('changed', [
    AFTER.replace(b'LIMIT = 12', b'LIMIT = 11'),
    AFTER.replace(b'import GridPolicy', b'import PhysicalTableIR'),
    AFTER.replace(b'physical.physical_models', b'structure.models'),
    AFTER + b'print("changed")\n',
    b'not valid python !!!',
])
def test_freeze_rejects_behavior_and_dependency_changes(changed):
    assert not equivalent_source(BEFORE, changed, OLD, NEW)


def fixture_root(tmp_path):
    folder = tmp_path / 'reports/parser-freeze-20261105'
    folder.mkdir(parents=True)
    archive = folder / 'baseline.zip'
    with zipfile.ZipFile(archive, 'w') as stream:
        stream.writestr(OLD, BEFORE)
    artifact = tmp_path / 'saved-kb.json'
    artifact.write_bytes(b'{"original": true}')
    manifest = {
        'protected_hashes': {OLD: hashlib.sha256(BEFORE).hexdigest()},
        'artifact_hashes': {'saved-kb.json': hashlib.sha256(artifact.read_bytes()).hexdigest()},
        'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
    }
    (folder / 'manifest.json').write_text(json.dumps(manifest), encoding='utf8')
    target = tmp_path / NEW
    target.parent.mkdir(parents=True)
    target.write_bytes(AFTER)
    target.with_name('__init__.py').write_text('# 주석만 있는 패키지\n', encoding='utf8')
    return tmp_path


def test_original_manifest_and_archive_validate_the_moved_source(tmp_path):
    result = check(fixture_root(tmp_path))
    assert result['changed_or_missing'] == []
    assert result['relocated_or_commented_equivalent'] == [NEW]


@pytest.mark.parametrize('target, contents', [
    ('saved-kb.json', b'{}'),
    ('reports/parser-freeze-20261105/baseline.zip', b'invalid archive'),
    (NEW, b'LIMIT = 11'),
    (str(Path(NEW).with_name('__init__.py')).replace('\\', '/'), b'import os\n'),
])
def test_freeze_still_rejects_source_artifact_and_package_mutation(tmp_path, target, contents):
    root = fixture_root(tmp_path)
    (root / target).write_bytes(contents)
    assert target in check(root)['changed_or_missing']
