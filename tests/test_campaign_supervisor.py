import json
import sqlite3

import pytest

from doc_understanding.agents.workflows.campaign.service import execute, execute_local_cycle
from doc_understanding.agents.workflows.campaign.store import CampaignStore


def test_campaign_processes_samples_and_keeps_holdout_separate(tmp_path):
    store = CampaignStore(tmp_path / "campaigns")
    cid = store.create("Evaluate table structure")
    files = []
    for n in range(3):
        path = tmp_path / f"{n}.pdf"
        path.write_bytes(b"%PDF-" + bytes([n]))
        files.append(path)
    for path, role in zip(files, ("development", "regression", "holdout")):
        store.add_sample(cid, path, role)
    def fake_worker(source, out):
        (out / "result.json").write_text(json.dumps({"document_id":"example", "pages":[], "tables":[]}), encoding="utf8")
    result = execute(store, cid, process=fake_worker)
    record = CampaignStore(tmp_path / "campaigns").get(cid)
    assert result["reason"] == "specialist_review_not_connected"
    assert [s["status"] for s in record["samples"]] == ["completed", "completed", "pending"]
    assert record["status"] == "paused"
    assert [e["team"] for e in record["events"]].count("parse") == 2
    assert [s["review_status"] for s in record["samples"]] == ["completed", "completed", None]
    with pytest.raises(ValueError, match="CAMPAIGN_NOT_READY"):
        execute(store, cid, process=fake_worker)


def test_changed_source_is_blocked_and_failure_is_recorded(tmp_path):
    store = CampaignStore(tmp_path / "campaigns")
    cid = store.create("Check source")
    source = tmp_path / "input.pdf"
    source.write_bytes(b"%PDF-original")
    store.add_sample(cid, source, "development")
    source.write_bytes(b"%PDF-changed")
    result = execute(store, cid, process=lambda *args: pytest.fail("must not run"))
    sample = store.samples(cid)[0]
    assert sample["status"] == "failed"
    assert sample["error"] == "SOURCE_HASH_MISMATCH"
    assert result["reason"] == "sample_processing_failed"


def test_same_document_cannot_be_registered_in_two_roles(tmp_path):
    store = CampaignStore(tmp_path / "campaigns")
    cid = store.create("Separate test data")
    source = tmp_path / "input.pdf"
    source.write_bytes(b"%PDF-example")
    store.add_sample(cid, source, "development")
    with pytest.raises(sqlite3.IntegrityError):
        store.add_sample(cid, source, "holdout")


def test_real_document_worker_produces_campaign_result(tmp_path):
    store = CampaignStore(tmp_path / "campaigns")
    cid = store.create("Process local example")
    store.add_sample(cid, "reports/physical-fixtures/horizontal.pdf", "development")
    execute(store, cid)
    sample = store.samples(cid)[0]
    assert sample["status"] == "completed"
    from pathlib import Path
    result = json.loads((Path(sample["output"]) / "document.json").read_text(encoding="utf8"))
    assert result["physical_tables"]


def test_automatic_cycle_collects_only_inbox_and_stops_at_review(tmp_path):
    store = CampaignStore(tmp_path / "work")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "one.pdf").write_bytes(b"%PDF-one")
    (inbox / "duplicate.pdf").write_bytes(b"%PDF-one")
    (inbox / "invalid.pdf").write_bytes(b"not pdf")
    nested = inbox / "holdout"
    nested.mkdir()
    (nested / "protected.pdf").write_bytes(b"%PDF-holdout")

    def process(source, out):
        (out / "result.json").write_text(json.dumps({"document_id":"example", "pages":[], "tables":[]}), encoding="utf8")

    cid = execute_local_cycle(store, inbox, process=process, discover_sources=lambda *_: None)
    record = store.get(cid)
    assert len(record["samples"]) == 1
    assert record["samples"][0]["role"] == "development"
    assert record["samples"][0]["status"] == "completed"
    assert record["events"][-1]["reason"] == "specialist_review_not_connected"
    assert [e["team"] for e in record["events"]][:2] == ["supervisor", "collect"]


def test_automatic_cycle_stops_if_no_samples(tmp_path):
    store = CampaignStore(tmp_path / "work")
    cid = execute_local_cycle(store, tmp_path / "empty", process=lambda *_: pytest.fail("no sample"), discover_sources=lambda *_: None)
    record = store.get(cid)
    assert record["events"][-1]["reason"] == "no_development_or_regression_samples"


def test_local_review_records_anchored_signals_without_claiming_accuracy(tmp_path):
    from doc_understanding.agents.workflows.campaign.review import save_review

    (tmp_path / "result.json").write_text(json.dumps({
        "document_id": "doc1", "pages": [{"number": 2, "text_status": "ocr_required"}],
        "tables": [{"id": "table1", "page": 2, "state": "partial", "cells": [
            {"id": "cell1", "state": "ambiguous"}, {"id": "cell2", "state": "resolved"}
        ]}]
    }), encoding="utf8")
    review = save_review(tmp_path)
    assert [f["code"] for f in review["findings"]] == [
        "OCR_REQUIRED", "STRUCTURE_NOT_VALIDATED", "CELL_PATH_UNCERTAIN"]
    assert review["findings"][-1]["cell_id"] == "cell1"
    assert not review["accuracy_verified"] and not review["llm_review_performed"]
    assert json.loads((tmp_path / "campaign-review.json").read_text(encoding="utf8")) == review


def test_invalid_export_stops_before_specialist_review(tmp_path):
    store = CampaignStore(tmp_path / "work")
    cid = store.create("invalid export")
    pdf = tmp_path / "one.pdf"
    pdf.write_bytes(b"%PDF-test")
    store.add_sample(cid, pdf, "development")
    def process(source, out):
        (out / "result.json").write_text("{}", encoding="utf8")
    result = execute(store, cid, process=process)
    assert result["reason"] == "local_review_incomplete"
    assert store.samples(cid)[0]["review_status"] == "failed"


def test_twenty_samples_fit_bounded_graph(tmp_path):
    store = CampaignStore(tmp_path / "work")
    cid = store.create("full batch")
    for n in range(20):
        pdf = tmp_path / f"{n}.pdf"
        pdf.write_bytes(b"%PDF-" + str(n).encode())
        store.add_sample(cid, pdf, "development")
    def process(source, out):
        (out / "result.json").write_text(json.dumps({"document_id":"d", "pages":[], "tables":[]}), encoding="utf8")
    result = execute(store, cid, process=process)
    assert result["reason"] == "specialist_review_not_connected"
    assert all(sample["review_status"] == "completed" for sample in store.samples(cid))
