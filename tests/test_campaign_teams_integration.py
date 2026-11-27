import json

import fitz
import pytest

from doc_understanding.runtime.review_queue import ReviewQueue
from doc_understanding.services.documents import worker
from doc_understanding.agents.workflows.campaign.public_download import collect_public_pdfs
from doc_understanding.agents.workflows.campaign.review_requests import prepare_requests
from doc_understanding.agents.workflows.campaign.service import execute
from doc_understanding.agents.workflows.campaign.store import CampaignStore


def pdf_bytes():
    doc = fitz.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


def test_public_collection_registers_provenance_and_skips_unlicensed(tmp_path, monkeypatch):
    monkeypatch.setenv('DOCUMENT_PUBLIC_SOURCE_CATALOG', 'config/examples/public-sources.json')
    store = CampaignStore(tmp_path / "w")
    cid = store.create("collect")
    source = {"id": "krihs_reports", "organization": "국토연구원", "domain": "real_estate"}
    store.add_candidate(cid, source, "https://kremap.krihs.re.kr/menu6/ReportView?bid=1", "보고서")
    store.add_candidate(cid, source, "https://kremap.krihs.re.kr/menu6/ReportView?bid=2", "미확인")
    def page(url):
        if url.endswith("=2"):
            return '<a href="/File/unverified.pdf">PDF</a>'
        return ('<p>국토연구원이 창작한 저작물은 공공누리 출처표시 조건</p>'
                '<a href="/File/report.pdf">PDF</a>'
                '<a href="https://evil.example/test.pdf">외부</a>')
    assert collect_public_pdfs(store, cid, fetch_page=page, fetch_pdf=lambda url: pdf_bytes()) == 1
    record = store.get(cid)
    assert len(record["samples"]) == 1
    assert record["samples"][0]["role"] == "development"
    with store.db() as db:
        meta = json.loads(db.execute("SELECT metadata_json FROM public_sample_provenance").fetchone()[0])
    assert meta["pdf_url"] == "https://kremap.krihs.re.kr/File/report.pdf"
    assert meta["sha256"] == record["samples"][0]["sha256"]
    assert "rights_unconfirmed" in [e["reason"] for e in record["events"]]
    assert collect_public_pdfs(store, cid, fetch_page=page, fetch_pdf=lambda url: pdf_bytes()) == 0


def test_real_pdf_creates_source_pinned_deduplicated_review_request(tmp_path):
    store = CampaignStore(tmp_path / "w")
    cid = store.create("review")
    store.add_sample(cid, "reports/physical-fixtures/horizontal.pdf", "development")

    def extract_with_signal(source, out):
        worker(source, out)
        path = out / "result.json"
        result = json.loads(path.read_text(encoding="utf8"))
        assert result["tables"]
        result["tables"][0]["state"] = "partial"
        path.write_text(json.dumps(result), encoding="utf8")

    execute(store, cid, process=extract_with_signal)
    record = store.get(cid)
    assert len(record["review_requests"]) == 1
    rid = record["review_requests"][0]["request_id"]
    queue = ReviewQueue(store.root)
    detail = queue.detail(rid)
    assert detail["status"] == "completed"
    assert detail["mode"] == "local"
    assert prepare_requests(store, cid) == 0
    assert len(store.get(cid)["review_requests"]) == 1


def test_existing_holdout_cannot_be_reused_as_development(tmp_path):
    store = CampaignStore(tmp_path / "w")
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(pdf_bytes())
    holdout = store.create("holdout")
    store.add_sample(holdout, pdf, "holdout")
    development = store.create("development")
    with pytest.raises(ValueError, match="DATASET_ROLE_CONFLICT"):
        store.add_sample(development, pdf, "development")
