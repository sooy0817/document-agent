import importlib.util
import tempfile
import unittest
from pathlib import Path

from tests.test_workflow import FakeExtractor


@unittest.skipUnless(importlib.util.find_spec("fastapi") and importlib.util.find_spec("httpx"),
                     "FastAPI/httpx unavailable: HTTP integration not executed")
class APITests(unittest.TestCase):
    def test_health_and_ir_endpoint(self):
        from fastapi.testclient import TestClient
        from doc_understanding.api.app import create_app
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(create_app(FakeExtractor(), Path(directory))) as client:
                self.assertEqual(client.get("/health").status_code, 200)
                self.assertEqual(client.post("/v1/ir/extract", content=b"bad").status_code, 415)
                response = client.post("/v1/ir/extract", content=b"%PDF-fixture",
                                       headers={"Content-Type":"application/pdf"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["validation_status"], "not_validated")

    def test_bad_pdf_rejected(self):
        from fastapi.testclient import TestClient
        from doc_understanding.api.app import create_app
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(create_app(FakeExtractor(), Path(directory))) as client:
                response = client.post("/v1/ir/extract", content=b"bad",
                                       headers={"Content-Type":"application/pdf"})
                self.assertEqual(response.status_code, 422)
