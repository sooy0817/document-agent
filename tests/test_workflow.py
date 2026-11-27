import json
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from doc_understanding.domain.contracts import ExtractionError, ExtractionLimits, ExtractionRequest, PDFExtractor
from doc_understanding.storage.ingestion.artifacts import LocalArtifactStore
from doc_understanding.parser.orchestration.workflow import extract_ir
from tests.support import sample_ir


class FakeExtractor:
    name, version = "fake", "1"

    def extract(self, request, store, run):
        store.write("source.pdf", request.pdf_bytes, "application/pdf")
        return sample_ir(run, data=request.pdf_bytes)


class WorkflowTests(unittest.TestCase):
    def test_adapter_replaceable_and_runs_distinct(self):
        self.assertIsInstance(FakeExtractor(), PDFExtractor)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = ExtractionRequest(b"%PDF-fixture", "fixture.pdf")
            a = extract_ir(request, FakeExtractor(), root)
            b = extract_ir(request, FakeExtractor(), root)
            self.assertEqual(a.document_id, b.document_id)
            self.assertNotEqual(a.run.run_id, b.run.run_id)
            self.assertEqual((root / str(a.run.run_id) / "source.pdf").read_bytes(), request.pdf_bytes)
            output = json.loads((root / str(a.run.run_id) / "document-ir.json").read_text(encoding="utf-8"))
            self.assertEqual(output["validation_status"], "not_validated")

    def test_size_and_signature_rejected_before_adapter(self):
        for request, code in [(ExtractionRequest(b"bad", "x"), "INVALID_PDF"),
                              (ExtractionRequest(b"%PDF-long", "x", limits=ExtractionLimits(max_bytes=5)), "FILE_TOO_LARGE")]:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ExtractionError) as context:
                    extract_ir(request, FakeExtractor(), Path(directory))
                self.assertEqual(context.exception.code, code)
                self.assertFalse(list(Path(directory).iterdir()))

    def test_failed_run_recorded_without_sensitive_error(self):
        class Broken(FakeExtractor):
            def extract(self, *args):
                raise RuntimeError("private PDF contents")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):
                extract_ir(ExtractionRequest(b"%PDF-f", "x"), Broken(), Path(directory))
            content = next(Path(directory).rglob("failure.json")).read_text()
            self.assertNotIn("private", content)
            self.assertEqual(json.loads(content)["code"], "EXTRACTION_FAILED")

    def test_invalid_adapter_provenance_rejected(self):
        class Invalid(FakeExtractor):
            def extract(self, request, store, run):
                return sample_ir(run).model_copy(update={"document_id":"wrong"})
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                extract_ir(ExtractionRequest(b"%PDF-f", "x"), Invalid(), Path(directory))

    def test_adapter_cannot_substitute_different_pdf(self):
        class Substituted(FakeExtractor):
            def extract(self, request, store, run):
                return sample_ir(run, data=b"%PDF-other")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ExtractionError) as context:
                extract_ir(ExtractionRequest(b"%PDF-input", "x"), Substituted(), Path(directory))
            self.assertEqual(context.exception.code, "ADAPTER_SOURCE_MISMATCH")


class ArtifactTests(unittest.TestCase):
    def test_hash_and_append_only(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalArtifactStore(Path(directory))
            result = store.write("pages/one.png", b"evidence", "image/png")
            self.assertEqual(result.sha256, sha256(b"evidence").hexdigest())
            with self.assertRaises(FileExistsError):
                store.write("pages/one.png", b"replacement", "image/png")

    def test_traversal_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalArtifactStore(Path(directory))
            for key in ["../outside", "/outside", "C:/outside", "..\\outside", ""]:
                with self.subTest(key=key), self.assertRaises(ValueError):
                    store.write(key, b"x", "text/plain")
