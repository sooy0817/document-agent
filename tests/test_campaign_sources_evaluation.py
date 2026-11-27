import json
from urllib.error import URLError

import pytest

from doc_understanding.evaluation.parser.run_comparison import compare_runs, record_comparison
from doc_understanding.agents.workflows.campaign.public_sources import discover
from doc_understanding.agents.workflows.campaign.store import CampaignStore


def test_official_source_discovery_keeps_links_and_does_not_register_pdf(tmp_path, monkeypatch):
    monkeypatch.setenv('DOCUMENT_PUBLIC_SOURCE_CATALOG', 'config/examples/public-sources.json')
    store = CampaignStore(tmp_path)
    cid = store.create("한국 자료")
    html = '''<a href="/menu6/ReportView.aspx?Kind=81&amp;bID=9001">부동산 금융</a>
      <a href="https://bad.example/menu6/ReportView.aspx?x=1">외부</a>
      <a href="/menu6/ReportView.aspx?Kind=81&amp;bID=9001">중복</a>'''
    def fetch(url):
        if "kremap" in url:
            return html
        raise URLError("offline")
    discover(store, cid, fetch=fetch)
    campaign = store.get(cid)
    assert len(campaign["source_candidates"]) == 1
    candidate = campaign["source_candidates"][0]
    assert candidate["organization"] == "국토연구원"
    assert candidate["rights_status"] == "unverified" and candidate["status"] == "link_only"
    assert campaign["samples"] == []
    assert campaign["events"][-1]["reason"] == "bok_stability_source_unavailable"


def evaluated(version, values):
    return {"dataset_sha256": "d", "reference_sha256": "g", "metric_version": "v1",
            "reference_status": "human_confirmed", "parser_fingerprint": version,
            "metrics": values}


def test_comparison_records_improvement_and_regression_and_is_immutable(tmp_path):
    old = evaluated("old", {"column_path": {"correct": 3, "total": 4},
                            "exact_row_path": {"correct": 2, "total": 4}})
    new = evaluated("new", {"column_path": {"correct": 2, "total": 4},
                            "exact_row_path": {"correct": 3, "total": 4}})
    report = compare_runs(old, new)
    assert report["metrics"]["column_path"]["delta"] == -.25
    assert report["metrics"]["column_path"]["status"] == "regression"
    assert report["metrics"]["exact_row_path"]["status"] == "improved"
    assert not report["human_approval"] and not report["parser_applied"]
    path = record_comparison(tmp_path, old, new)
    assert record_comparison(tmp_path, old, new) == path
    assert json.loads(path.read_text(encoding="utf8"))["before"] == old


def test_comparison_refuses_unconfirmed_or_different_reference():
    old = evaluated("old", {"column_path": {"correct": 0, "total": 1}})
    new = evaluated("new", {"column_path": {"correct": 1, "total": 1}})
    new["reference_sha256"] = "different"
    with pytest.raises(ValueError, match="INCOMPARABLE_REFERENCE"):
        compare_runs(old, new)
    new["reference_sha256"] = "g"
    new["reference_status"] = "proposed"
    with pytest.raises(ValueError, match="REFERENCE_NOT_HUMAN_CONFIRMED"):
        compare_runs(old, new)
