"""Explicit, offline campaign execution using the existing document worker."""

import hashlib

from doc_understanding.services.documents import worker
from .collection import collect_local
from .graph import build_graph
from .public_sources import discover
from .public_download import collect_public_pdfs
from .review import save_review
from .review_requests import prepare_requests
from .locking import campaign_lock


def execute(store, campaign_id, *, process=worker, specialist=None, resume=False):
    # IDs are looked up before constructing filesystem paths.
    store.get(campaign_id)
    with campaign_lock(store.root / (campaign_id + ".lock")):
        return _execute(store, campaign_id, process=process, specialist=specialist, resume=resume)


def _execute(store, campaign_id, *, process, specialist, resume):
    with store.db() as db:
        row = db.execute("SELECT status FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        allowed = {"ready", "paused", "blocked", "running"} if resume else {"ready"}
        if not row or row["status"] not in allowed:
            raise ValueError("CAMPAIGN_NOT_READY")
        db.execute("UPDATE campaigns SET status='running' WHERE id=?", (campaign_id,))

    def parse(cid, sample_id):
        with store.db() as db:
            row = db.execute("SELECT * FROM samples WHERE id=? AND campaign_id=? AND status='pending'", (sample_id, cid)).fetchone()
            if not row:
                raise ValueError("SAMPLE_NOT_PENDING")
            sample = dict(row)
            db.execute("UPDATE samples SET status='running' WHERE id=?", (sample_id,))
        # Sample IDs are globally unique. One directory level also avoids Windows MAX_PATH
        # when the existing extractor creates its nested physical evidence paths.
        out = store.root / sample_id
        try:
            from pathlib import Path
            data = Path(sample["path"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != sample["sha256"]:
                raise ValueError("SOURCE_HASH_MISMATCH")
            out.mkdir(parents=True, exist_ok=False)
            source = out / "input.pdf"
            source.write_bytes(data)
            process(source, out)
            if not (out / "result.json").is_file():
                raise ValueError("RESULT_MISSING")
            with store.db() as db:
                db.execute("UPDATE samples SET status='completed',output=? WHERE id=?", (str(out), sample_id))
            store.event(cid, "parse", "completed", sample_id)
        except Exception as exc:
            code = str(exc) if isinstance(exc, ValueError) and str(exc) in ("SOURCE_HASH_MISMATCH", "RESULT_MISSING") else "PROCESSING_FAILED"
            with store.db() as db:
                db.execute("UPDATE samples SET status='failed',error=? WHERE id=?", (code, sample_id))
            store.event(cid, "parse", code, sample_id)

    def review(cid, sample_id):
        with store.db() as db:
            row = db.execute("SELECT output FROM samples WHERE id=? AND campaign_id=? AND status='completed'", (sample_id, cid)).fetchone()
            if not row:
                raise ValueError("SAMPLE_NOT_COMPLETED")
            db.execute("INSERT INTO campaign_reviews(sample_id,status) VALUES(?,?)", (sample_id, "running"))
        try:
            report = save_review(row["output"])
            with store.db() as db:
                db.execute("UPDATE campaign_reviews SET status='completed',findings_count=? WHERE sample_id=?", (len(report["findings"]), sample_id))
            store.event(cid, "review", "local_review_completed", sample_id)
        except Exception:
            with store.db() as db:
                db.execute("UPDATE campaign_reviews SET status='failed',error='LOCAL_REVIEW_FAILED' WHERE sample_id=?", (sample_id,))
            store.event(cid, "review", "local_review_failed", sample_id)

    graph = build_graph(store, parse, review, specialist=specialist)
    result = graph.invoke({"campaign_id": campaign_id}, config={"recursion_limit": 128})
    if result.get("reason") == "specialist_review_not_connected":
        prepare_requests(store, campaign_id)
    with store.db() as db:
        status = "blocked" if result.get("reason", "").startswith("specialist_blocked") else "paused"
        db.execute("UPDATE campaigns SET status=? WHERE id=?", (status, campaign_id))
    return result


def execute_local_cycle(store, inbox, *, process=worker, discover_sources=discover,
                        download_sources=collect_public_pdfs):
    """Discover public candidates and local samples, then process local samples."""
    campaign_id = store.create("자동 표 구조 개선 평가")
    try:
        store.event(campaign_id, "supervisor", "collect_local_development_samples")
        collect_local(store, campaign_id, inbox)
        discover_sources(store, campaign_id)
        download_sources(store, campaign_id)
        execute(store, campaign_id, process=process)
    except Exception:
        with store.db() as db:
            db.execute("UPDATE campaigns SET status='failed' WHERE id=?", (campaign_id,))
        store.event(campaign_id, "supervisor", "automatic_cycle_failed")
        raise
    return campaign_id
