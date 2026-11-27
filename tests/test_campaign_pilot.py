"""Control-flow tests use injected responses; they are not live model validation."""

import copy
from pathlib import Path

import pytest

from doc_understanding.review import error_basket
from doc_understanding.runtime.review_queue import ReviewQueue
from doc_understanding.services.documents import worker
from doc_understanding.agents.workflows.campaign import pilot
from doc_understanding.agents.workflows.campaign.locking import campaign_lock
from doc_understanding.agents.workflows.campaign.store import CampaignStore


@pytest.fixture
def selection(tmp_path):
    source = Path("reports/physical-fixtures/horizontal.pdf")
    original = tmp_path / "original"
    original.mkdir()
    (original / "input.pdf").write_bytes(source.read_bytes())
    worker(original / "input.pdf", original)
    doc = pilot.read(original / "document.json")
    table = doc["physical_tables"][0]
    item = error_basket.collect(tmp_path / "basket", original,
        dict(id="a" * 32, name="pilot fixture", pages=1, status="done"),
        error_basket.CollectionRequest(page=table["page_number"], scope="table",
                                      table_id=table["table_id"], error_type="other"))
    snapshot = tmp_path / "basket" / item["id"] / "snapshot.json"
    snap = pilot.read(snapshot)
    manifest = dict(role="development", documents=[dict(label="N03", role="development",
        source_path="original/input.pdf", source_sha256=pilot.sha(original / "input.pdf"),
        canonical_path="original/document.json", canonical_sha256=pilot.sha(original / "document.json"))],
        items=[dict(item_id=item["id"], document="N03", role="development", request_id="historical",
                    snapshot_path=snapshot.relative_to(tmp_path).as_posix(),
                    snapshot_sha256=pilot.sha(snapshot), selection=snap["selection"])])
    path = tmp_path / "manifest.json"
    pilot.save(path, manifest)
    return path


def completed(record, data, progress, rid):
    assert record["execution_key"]
    progress("classify_roles")
    return dict(call_records=[dict(status="completed", output={"test_only": True}) for _ in range(3)],
                api_calls=3, verdict="suspected_issue", semantic_verified=False)


def test_real_parser_connects_specialist_and_resume_does_not_duplicate(tmp_path, selection):
    store = CampaignStore(tmp_path / "w")
    cid = pilot.prepare(store, selection, tmp_path)
    def queue(p):
        return ReviewQueue(p, agent_checker=completed)
    result = pilot.run(store, cid, tmp_path, queue_factory=queue)
    assert result["reason"] == "improvement_not_connected"
    record = store.get(cid)
    assert record["status"] == "paused"
    assert len(record["review_requests"]) == 1
    rid = record["review_requests"][0]["request_id"]
    assert queue(store.root).detail(rid)["mode"] == "agents_roles"
    assert queue(store.root).detail(rid)["attempt"] == 1
    assert pilot.prepare(store, selection, tmp_path) == cid
    pilot.run(store, cid, tmp_path, resume=True, queue_factory=queue)
    assert store.get(cid)["review_requests"][0]["request_id"] == rid
    assert queue(store.root).detail(rid)["attempt"] == 1
    report = pilot.read(store.root / "pilots" / cid / "execution.json")
    assert not report["human_approved"] and not report["parser_modified"]


def test_failure_blocks_and_only_explicit_retry_creates_new_request(tmp_path, selection):
    store = CampaignStore(tmp_path / "w")
    cid = pilot.prepare(store, selection, tmp_path)
    def fail(*args):
        raise RuntimeError("transport failed")
    def bad(p):
        return ReviewQueue(p, agent_checker=fail)
    pilot.run(store, cid, tmp_path, queue_factory=bad)
    assert store.get(cid)["status"] == "blocked"
    old = store.get(cid)["review_requests"][0]["request_id"]
    def good(p):
        return ReviewQueue(p, agent_checker=completed)
    pilot.run(store, cid, tmp_path, resume=True, queue_factory=good)
    assert store.get(cid)["review_requests"][0]["request_id"] == old
    assert store.get(cid)["status"] == "blocked"
    pilot.run(store, cid, tmp_path, resume=True, retry_failed=True, queue_factory=good)
    assert store.get(cid)["status"] == "paused"
    assert store.get(cid)["review_requests"][0]["request_id"] != old
    assert good(store.root).detail(old)["status"] == "failed"


def test_changed_original_is_rejected_before_registration(tmp_path, selection):
    (tmp_path / "original/input.pdf").write_bytes(b"%PDF-changed")
    store = CampaignStore(tmp_path / "w")
    with pytest.raises(ValueError, match="PILOT_SOURCE_CHANGED"):
        pilot.prepare(store, selection, tmp_path)
    assert not store.list_campaigns()


def test_fresh_selection_uses_geometry_not_old_id(tmp_path, selection):
    manifest = pilot.read(selection)
    snap = pilot.read(tmp_path / manifest["items"][0]["snapshot_path"])
    doc = pilot.read(tmp_path / "original/document.json")
    table = doc["physical_tables"][0]
    old_id = table["table_id"]
    table["table_id"] = "new-correct-id"
    unrelated = copy.deepcopy(table)
    unrelated["table_id"] = old_id
    unrelated["bounding_box"] = dict(x0=0, y0=0, x1=1, y1=1)
    doc["physical_tables"].append(unrelated)
    assert pilot.rebind(snap, doc).table_id == "new-correct-id"
    doc["physical_tables"].append(copy.deepcopy(table))
    with pytest.raises(ValueError, match="AMBIGUOUS"):
        pilot.rebind(snap, doc)


def test_local_result_cannot_count_as_completed_model_review(tmp_path, selection):
    store = CampaignStore(tmp_path / "w")
    cid = pilot.prepare(store, selection, tmp_path)
    def queue(p):
        return ReviewQueue(p, agent_checker=lambda *args: dict(api_calls=0))
    result = pilot.run(store, cid, tmp_path, queue_factory=queue)
    assert result["reason"] == "specialist_blocked:review_incomplete"
    assert store.get(cid)["status"] == "blocked"


def test_lock_rejects_second_worker_and_releases(tmp_path):
    path = tmp_path / "worker.lock"
    with campaign_lock(path):
        with pytest.raises(ValueError, match="ALREADY_RUNNING"):
            with campaign_lock(path):
                pytest.fail("concurrent worker acquired lock")
    with campaign_lock(path):
        assert path.exists()


def test_manifest_paths_cannot_escape_project(tmp_path):
    with pytest.raises(ValueError, match="RELATIVE"):
        pilot.local_path(tmp_path, str(tmp_path.resolve()))


@pytest.mark.parametrize('corrupt', [False, True])
def test_rule_only_completion_requires_exact_cell_coverage(tmp_path, selection, corrupt):
    store = CampaignStore(tmp_path / 'w')
    cid = pilot.prepare(store, selection, tmp_path)
    def rules(record, data, progress, rid):
        snap = record['snapshot']
        doc = pilot.read(Path(data) / snap['job_id'] / 'document.json')
        table = next(t for t in doc['physical_tables'] if t['table_id'] == snap['selection']['table_id'])
        ids = [c['cell_id'] for c in table['cells']]
        outcomes = [dict(cell_id=c, status='rule_candidate') for c in ids]
        if corrupt:
            outcomes[-1]['cell_id'] = outcomes[0]['cell_id']
        return dict(review_complete=True, call_records=[], api_calls=0, outcomes=outcomes,
                    routing_summary=dict(total=len(ids), rule_retained=len(ids), llm_completed=0))
    result = pilot.run(store, cid, tmp_path, queue_factory=lambda p: ReviewQueue(p, agent_checker=rules))
    assert result['reason'] == ('specialist_blocked:review_incomplete' if corrupt else 'improvement_not_connected')
    evidence = pilot.read(store.root / 'pilots' / cid / 'specialist.json')['items'][0]
    assert evidence['specialist_review_completed'] is (not corrupt)
    assert evidence['model_review_completed'] is False
