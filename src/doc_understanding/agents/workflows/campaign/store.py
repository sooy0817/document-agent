"""Local campaign records and immutable sample metadata."""

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path


class CampaignStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "campaigns.sqlite3"
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS campaigns(
                    id TEXT PRIMARY KEY, goal TEXT NOT NULL, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS samples(
                    id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, path TEXT NOT NULL,
                    sha256 TEXT NOT NULL, role TEXT NOT NULL, status TEXT NOT NULL,
                    error TEXT, output TEXT, UNIQUE(campaign_id, sha256));
                CREATE TABLE IF NOT EXISTS events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id TEXT NOT NULL,
                    team TEXT NOT NULL, reason TEXT NOT NULL, sample_id TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS campaign_reviews(
                    sample_id TEXT PRIMARY KEY, status TEXT NOT NULL,
                    findings_count INTEGER, error TEXT);
                CREATE TABLE IF NOT EXISTS source_candidates(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id TEXT NOT NULL,
                    source_id TEXT NOT NULL, organization TEXT NOT NULL, domain TEXT NOT NULL,
                    url TEXT NOT NULL, title TEXT NOT NULL, rights_status TEXT NOT NULL,
                    status TEXT NOT NULL, discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(campaign_id, url));
                CREATE TABLE IF NOT EXISTS public_sample_provenance(
                    sample_id TEXT PRIMARY KEY, candidate_id INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS campaign_review_requests(
                    sample_id TEXT NOT NULL, page INTEGER NOT NULL, table_id TEXT NOT NULL,
                    item_id TEXT NOT NULL, request_id TEXT NOT NULL,
                    PRIMARY KEY(sample_id,page,table_id));
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create(self, goal):
        if not goal.strip() or len(goal) > 1000:
            raise ValueError("INVALID_GOAL")
        ident = uuid.uuid4().hex
        with self.db() as db:
            db.execute("INSERT INTO campaigns VALUES(?,?,?)", (ident, goal.strip(), "ready"))
        return ident

    def add_sample(self, campaign_id, source, role):
        if role not in ("development", "regression", "holdout"):
            raise ValueError("INVALID_SAMPLE_ROLE")
        source = Path(source).resolve(strict=True)
        data = source.read_bytes()
        if not data.startswith(b"%PDF-") or len(data) > 20 * 1024 * 1024:
            raise ValueError("INVALID_PDF")
        ident, digest = uuid.uuid4().hex, hashlib.sha256(data).hexdigest()
        with self.db() as db:
            campaign = db.execute("SELECT status FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
            if not campaign or campaign["status"] != "ready":
                raise ValueError("CAMPAIGN_NOT_READY")
            roles = {r[0] for r in db.execute("SELECT DISTINCT role FROM samples WHERE sha256=?", (digest,))}
            if roles - {role} and not db.execute(
                    "SELECT 1 FROM samples WHERE campaign_id=? AND sha256=?", (campaign_id, digest)).fetchone():
                raise ValueError("DATASET_ROLE_CONFLICT")
            if db.execute("SELECT count(*) FROM samples WHERE campaign_id=?", (campaign_id,)).fetchone()[0] >= 20:
                raise ValueError("SAMPLE_LIMIT")
            db.execute("INSERT INTO samples VALUES(?,?,?,?,?,?,NULL,NULL)",
                       (ident, campaign_id, str(source), digest, role, "pending"))
        return ident

    def samples(self, campaign_id):
        with self.db() as db:
            return [dict(r) for r in db.execute("""
                SELECT s.*, r.status AS review_status, r.findings_count AS review_findings_count,
                       r.error AS review_error
                FROM samples s LEFT JOIN campaign_reviews r ON r.sample_id=s.id
                WHERE s.campaign_id=? ORDER BY s.rowid
            """, (campaign_id,))]

    def event(self, campaign_id, team, reason, sample_id=""):
        with self.db() as db:
            db.execute("INSERT INTO events(campaign_id,team,reason,sample_id) VALUES(?,?,?,?)",
                       (campaign_id, team, reason, sample_id))

    def add_candidate(self, campaign_id, source, url, title):
        with self.db() as db:
            cur = db.execute("""INSERT OR IGNORE INTO source_candidates
                (campaign_id,source_id,organization,domain,url,title,rights_status,status)
                VALUES(?,?,?,?,?,?,?,?)""", (campaign_id, source["id"], source["organization"],
                source["domain"], url, title[:300], "unverified", "link_only"))
            return cur.rowcount == 1

    def add_public_sample(self, campaign_id, source, candidate_id, metadata):
        source = Path(source).resolve(strict=True)
        data = source.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        if sha != metadata.get("sha256") or not data.startswith(b"%PDF-") or len(data) > 20 * 1024 * 1024:
            raise ValueError("PUBLIC_SOURCE_INVALID")
        ident = uuid.uuid4().hex
        with self.db() as db:
            c = db.execute("SELECT status FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
            candidate = db.execute("SELECT id FROM source_candidates WHERE id=? AND campaign_id=?", (candidate_id, campaign_id)).fetchone()
            if not c or c["status"] != "ready" or not candidate:
                raise ValueError("CAMPAIGN_OR_CANDIDATE_INVALID")
            if db.execute("SELECT 1 FROM samples WHERE sha256=? AND role IN ('regression','holdout')", (sha,)).fetchone():
                raise ValueError("DATASET_ROLE_CONFLICT")
            if db.execute("SELECT 1 FROM samples WHERE campaign_id=? AND sha256=?", (campaign_id, sha)).fetchone():
                return None
            if db.execute("SELECT count(*) FROM samples WHERE campaign_id=?", (campaign_id,)).fetchone()[0] >= 20:
                raise ValueError("SAMPLE_LIMIT")
            db.execute("INSERT INTO samples VALUES(?,?,?,?,?,?,NULL,NULL)",
                       (ident, campaign_id, str(source), sha, "development", "pending"))
            db.execute("INSERT INTO public_sample_provenance VALUES(?,?,?)",
                       (ident, candidate_id, json.dumps(metadata, ensure_ascii=False)))
            db.execute("UPDATE source_candidates SET status='registered',rights_status='source_statement' WHERE id=?", (candidate_id,))
        return ident

    def has_review_request(self, sample_id, page, table_id):
        with self.db() as db:
            return bool(db.execute("SELECT 1 FROM campaign_review_requests WHERE sample_id=? AND page=? AND table_id=?", (sample_id, page, table_id)).fetchone())

    def add_review_request(self, sample_id, page, table_id, item_id, request_id):
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO campaign_review_requests VALUES(?,?,?,?,?)",
                       (sample_id, page, table_id, item_id, request_id))

    def get(self, campaign_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
            if not row:
                raise ValueError("CAMPAIGN_NOT_FOUND")
            events = [dict(r) for r in db.execute("SELECT * FROM events WHERE campaign_id=? ORDER BY id", (campaign_id,))]
            candidates = [dict(r) for r in db.execute(
                "SELECT * FROM source_candidates WHERE campaign_id=? ORDER BY id", (campaign_id,))]
            requests = [dict(r) for r in db.execute("""
                SELECT r.* FROM campaign_review_requests r JOIN samples s ON s.id=r.sample_id
                WHERE s.campaign_id=? ORDER BY s.rowid,r.page
            """, (campaign_id,))]
        return {**dict(row), "samples": self.samples(campaign_id), "events": events,
                "source_candidates": candidates, "review_requests": requests}

    def list_campaigns(self):
        with self.db() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM campaigns ORDER BY rowid DESC LIMIT 20")]
        return [self.get(ident) for ident in ids]
