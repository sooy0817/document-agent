"""Keep parser/runtime independent of retrieval and preserve packaged review assets."""
import ast
import importlib.util
from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'src/doc_understanding'


def test_parser_does_not_import_rag_evaluation_or_experiments():
    for path in (SOURCE / 'parser').rglob('*.py'):
        module = '.'.join(path.relative_to(ROOT / 'src').with_suffix('').parts)
        package = module.removesuffix('.__init__') if path.name == '__init__.py' else module.rpartition('.')[0]
        for node in ast.walk(ast.parse(path.read_bytes())):
            if isinstance(node, ast.ImportFrom):
                name = importlib.util.resolve_name('.' * node.level + (node.module or ''), package) if node.level else node.module or ''
                names = [name] + [name + '.' + alias.name for alias in node.names]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            for name in names:
                assert not any(name == prefix or name.startswith(prefix + '.') for prefix in (
                    'doc_understanding.rag', 'doc_understanding.evaluation', 'doc_understanding.experiments'
                )), (path, name)


def test_shared_telemetry_does_not_import_rag():
    tree = ast.parse((SOURCE / 'runtime/telemetry.py').read_bytes())
    assert not any(isinstance(n, ast.ImportFrom) and (n.module or '').startswith('doc_understanding.rag')
                   for n in ast.walk(tree))


def test_declared_package_assets_exist_after_moves():
    config = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf8'))
    for package, patterns in config['tool']['setuptools']['package-data'].items():
        directory = ROOT / 'src' / package.replace('.', '/')
        assert (directory / '__init__.py').is_file(), package
        for pattern in patterns:
            assert list(directory.glob(pattern)), (package, pattern)
