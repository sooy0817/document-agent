"""Persist observed document worker stages without changing document IR."""

import hashlib
import importlib.metadata
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def code_identity(package_root=None):
    """Fingerprint package source, not a frozen execution environment."""
    root = Path(package_root) if package_root else Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode("utf8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    try:
        pymupdf = importlib.metadata.version("PyMuPDF")
    except importlib.metadata.PackageNotFoundError:
        pymupdf = None
    return {"package_sha256": digest.hexdigest(), "python": sys.version.split()[0], "pymupdf": pymupdf}


class RunProgress:
    def __init__(self, source, output):
        self.path = Path(output) / "run-progress.json"
        self.started = time.monotonic()
        self.state = {
            "schema_version": "document-run-progress/1.0",
            "source_sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest(),
            "code_identity": code_identity(),
            "status": "running", "started_at": timestamp(),
            "stages": [
                {"id": "extract", "status": "pending"},
                {"id": "export", "status": "pending"},
                {"id": "review", "status": "not_connected"},
                {"id": "comparison", "status": "not_connected"},
            ],
        }
        self.write()

    def write(self):
        self.state["updated_at"] = timestamp()
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf8")
        for attempt in range(11):
            try:
                os.replace(temporary, self.path)
                break
            except PermissionError:
                if attempt == 10:
                    raise
                time.sleep(0.05)

    def stage(self, name, status):
        stage = next(item for item in self.state["stages"] if item["id"] == name)
        stage["status"] = status
        if status == "running":
            stage["started_at"] = timestamp()
            self.stage_started = time.monotonic()
        elif status in {"completed", "failed"}:
            stage["ended_at"] = timestamp()
            stage["seconds"] = round(time.monotonic() - self.stage_started, 3)
        self.write()

    def finish(self, status):
        self.state["code_changed_during_run"] = (
            code_identity()["package_sha256"] != self.state["code_identity"]["package_sha256"]
        )
        self.state.update(status=status, ended_at=timestamp(), seconds=round(time.monotonic() - self.started, 3))
        self.write()
