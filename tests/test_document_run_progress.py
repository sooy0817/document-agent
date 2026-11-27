import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from doc_understanding.services import documents
from doc_understanding.services import run_progress
from doc_understanding.services.run_progress import code_identity
from doc_understanding.parser import pipeline as prepare_module


def test_worker_records_observed_stages_and_source(tmp_path, monkeypatch):
    source = tmp_path / "input.pdf"
    source.write_bytes(b"example")
    monkeypatch.setattr(prepare_module, "prepare", lambda source, out: ("run", {"status": "ok"}))
    monkeypatch.setattr(documents, "export_run", lambda run, out, status: None)
    documents.worker(source, tmp_path)
    result = json.loads((tmp_path / "run-progress.json").read_text(encoding="utf8"))
    assert result["source_sha256"] == hashlib.sha256(b"example").hexdigest()
    assert len(result["code_identity"]["package_sha256"]) == 64
    assert result["status"] == "completed"
    assert [s["status"] for s in result["stages"]] == [
        "completed", "completed", "not_connected", "not_connected"
    ]


def test_failed_extraction_does_not_claim_export_or_review(tmp_path, monkeypatch):
    source = tmp_path / "input.pdf"
    source.write_bytes(b"example")
    def fail(*args):
        raise ValueError("sensitive source content")
    monkeypatch.setattr(prepare_module, "prepare", fail)
    with pytest.raises(ValueError):
        documents.worker(source, tmp_path)
    raw = (tmp_path / "run-progress.json").read_text(encoding="utf8")
    result = json.loads(raw)
    assert result["status"] == "failed"
    assert [s["status"] for s in result["stages"]] == [
        "failed", "pending", "not_connected", "not_connected"
    ]
    assert "sensitive source content" not in raw


def test_job_status_includes_progress_without_mutating_job(tmp_path, monkeypatch):
    from doc_understanding.web import app
    folder = tmp_path / "job"
    folder.mkdir()
    (folder / "run-progress.json").write_text('{"status":"running"}', encoding="utf8")
    monkeypatch.setattr(app, "DATA", tmp_path)
    monkeypatch.setattr(app, "JOBS", {"job": {"status": "running"}})
    assert app.status("job")["progress"]["status"] == "running"
    assert "progress" not in app.JOBS["job"]


def test_code_identity_changes_with_source(tmp_path):
    module = tmp_path / "extract.py"
    module.write_text("x = 1", encoding="utf8")
    original = code_identity(tmp_path)["package_sha256"]
    module.write_text("x = 2", encoding="utf8")
    assert code_identity(tmp_path)["package_sha256"] != original


def test_progress_recovers_from_transient_windows_file_lock(tmp_path, monkeypatch):
    source = tmp_path / "input.pdf"
    source.write_bytes(b"example")
    replace = run_progress.os.replace
    calls = []
    def flaky_replace(src, dst):
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError("temporarily locked")
        replace(src, dst)
    monkeypatch.setattr(run_progress.os, "replace", flaky_replace)
    monkeypatch.setattr(run_progress.time, "sleep", lambda seconds: None)
    run_progress.RunProgress(source, tmp_path)
    assert len(calls) == 2
    assert json.loads((tmp_path / "run-progress.json").read_text(encoding="utf8"))["status"] == "running"


def test_recover_incomplete_run_does_not_claim_success(tmp_path, monkeypatch):
    from doc_understanding.web import app
    folder = tmp_path / ("b" * 32)
    folder.mkdir()
    (folder / "input.pdf").write_bytes(b"example")
    (folder / "result.json").write_text('{"pages": []}', encoding="utf8")
    (folder / "run-progress.json").write_text('{"status":"running"}', encoding="utf8")
    monkeypatch.setattr(app, "DATA", tmp_path)
    monkeypatch.setattr(app, "JOBS", {})
    app.recover_jobs()
    assert app.JOBS[folder.name]["status"] == "unknown"
    assert app.status(folder.name)["progress"]["status"] == "running"
    assert app.document_runs()[0]["status"] == "unknown"


def test_recovery_shows_persisted_name_and_recorded_failure(tmp_path, monkeypatch):
    from doc_understanding.web import app
    folder = tmp_path / ("c" * 32)
    folder.mkdir()
    (folder / "job-info.json").write_text(
        json.dumps({"name": "sample.pdf", "pages": 3}), encoding="utf8"
    )
    (folder / "run-progress.json").write_text('{"status":"failed"}', encoding="utf8")
    monkeypatch.setattr(app, "DATA", tmp_path)
    monkeypatch.setattr(app, "JOBS", {})
    app.recover_jobs()
    assert app.JOBS[folder.name]["name"] == "sample.pdf"
    assert app.JOBS[folder.name]["pages"] == 3
    assert app.JOBS[folder.name]["status"] == "error"


def test_document_review_requests_only_show_matching_document(tmp_path, monkeypatch):
    from doc_understanding.web import app
    directory = tmp_path / "review-queue"
    directory.mkdir()
    with sqlite3.connect(directory / "queue.sqlite3") as db:
        db.execute("CREATE TABLE requests(id, status, stage, attempt, mode, payload, created_at)")
        for jid in ("one", "two"):
            db.execute("INSERT INTO requests VALUES(?,?,?,?,?,?,?)", (
                jid, "queued", "queued", 0, "local",
                json.dumps({"snapshot": {"job_id": jid}}), "2026"
            ))
    monkeypatch.setattr(app, "DATA", tmp_path)
    assert [r["id"] for r in app.document_review_requests("one")] == ["one"]


def test_retry_requires_failed_status_and_intact_original(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from doc_understanding.web import app
    folder = tmp_path / "job"
    folder.mkdir()
    pdf = b"original"
    (folder / "input.pdf").write_bytes(pdf)
    digest = hashlib.sha256(pdf).hexdigest()
    (folder / "source.json").write_text(json.dumps({
        "source_type": "pdf", "analysis_sha256": digest, "source_sha256": digest,
    }), encoding="utf8")
    monkeypatch.setattr(app, "DATA", tmp_path)
    monkeypatch.setattr(app, "JOBS", {"job": {"status": "error", "name": "a.pdf"}})
    monkeypatch.setattr(app, "authorize", lambda request: None)
    monkeypatch.setattr(app, "start", lambda data, name, **kwargs: (data, name, kwargs))
    assert app.retry_document("job", SimpleNamespace()) == (pdf, "a.pdf", {"parent_run_id": "job"})
    (folder / "input.pdf").write_bytes(b"changed")
    with pytest.raises(HTTPException) as exc:
        app.retry_document("job", SimpleNamespace())
    assert exc.value.status_code == 409
    app.JOBS["job"]["status"] = "unknown"
    with pytest.raises(HTTPException) as exc:
        app.retry_document("job", SimpleNamespace())
    assert exc.value.status_code == 409
