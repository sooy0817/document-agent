"""Build the parser sdist and wheel from the shared, unchanged parser sources."""
import argparse
from contextlib import chdir
from pathlib import Path
from shutil import copy2
from tempfile import TemporaryDirectory

from setuptools import build_meta


ROOT = Path(__file__).resolve().parents[1]
# Only these source files enter the distribution. In particular, the local
# artifact writer is needed by extraction, while storage/staging is DB code.
SOURCES = (
    "nh_document_parser/*.py",
    "doc_understanding/__init__.py",
    "doc_understanding/domain/__init__.py",
    "doc_understanding/domain/contracts.py",
    "doc_understanding/domain/models.py",
    "doc_understanding/domain/numeric.py",
    "doc_understanding/domain/pipeline_contracts.py",
    "doc_understanding/domain/text_markers.py",
    "doc_understanding/export/*.py",
    "doc_understanding/ocr/*.py",  # export imports it even when OCR is off
    "doc_understanding/parser/__init__.py",
    "doc_understanding/parser/contracts.py",
    "doc_understanding/parser/layout.py",
    "doc_understanding/parser/pipeline.py",
    "doc_understanding/parser/assembly/*.py",
    "doc_understanding/parser/extraction/__init__.py",
    "doc_understanding/parser/extraction/physical_evidence_adapter.py",
    "doc_understanding/parser/extraction/pymupdf_adapter.py",
    "doc_understanding/parser/orchestration/*.py",
    "doc_understanding/parser/physical/*.py",
    "doc_understanding/parser/relations/__init__.py",
    "doc_understanding/parser/relations/header_inheritance.py",
    "doc_understanding/parser/relations/logical_tables.py",
    "doc_understanding/parser/structure/*.py",
    "doc_understanding/storage/__init__.py",
    "doc_understanding/storage/ingestion/*.py",
)


def build(destination: Path):
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="nh-parser-build-") as temporary:
        project = Path(temporary)
        for pattern in SOURCES:
            files = sorted((ROOT / "src").glob(pattern))
            if not files:
                raise FileNotFoundError(pattern)
            for source in files:
                target = project / source.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                copy2(source, target)
        copy2(ROOT / "packaging/parser/pyproject.toml", project)
        copy2(ROOT / "packaging/parser/README.md", project)
        copy2(ROOT / "schemas/clean-document.schema.json", project / "src/nh_document_parser")
        with chdir(project):
            build_meta.build_sdist(str(destination))
            build_meta.build_wheel(str(destination))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "dist/parser")
    build(parser.parse_args().out)
