"""Persistent, single-process local review queue; basket and parser files are read-only."""

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from doc_understanding.review import error_basket
from doc_understanding.validation import source_integrity


def now():
    return datetime.now(timezone.utc).isoformat()


def pack(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class ReviewQueue:
    def __init__(self, data, checker=source_integrity.inspect, agent_checker=None):
        self.data = Path(data)
        self.basket = self.data / "error-basket"
        self.path = self.data / "review-queue" / "queue.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.checker = checker
        self.agent_checker = agent_checker
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.sync_error = None
        with self.db() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS requests (
                  id TEXT PRIMARY KEY, item_id TEXT NOT NULL, revision TEXT UNIQUE NOT NULL,
                  payload TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL,
                  attempt INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                  result TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS events (
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL,
                  at TEXT NOT NULL, kind TEXT NOT NULL, attempt INTEGER NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS decisions (
                  request_id TEXT PRIMARY KEY, child_id TEXT UNIQUE NOT NULL,
                  at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS confirmations (
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL,
                  scope_key TEXT NOT NULL, at TEXT NOT NULL, payload TEXT NOT NULL);
                INSERT OR IGNORE INTO settings VALUES ('paused', 'false');
            """)
            if "mode" not in {r["name"] for r in c.execute("PRAGMA table_info(requests)")}:
                c.execute("ALTER TABLE requests ADD COLUMN mode TEXT NOT NULL DEFAULT 'local'")
            if "parent_id" not in {r["name"] for r in c.execute("PRAGMA table_info(requests)")}:
                c.execute("ALTER TABLE requests ADD COLUMN parent_id TEXT")

    def agent_settings(self):
        path = self.data / "review-agent-config.json"
        config = json.loads(path.read_text(encoding="utf8")) if path.exists() else {}
        return dict(
            enabled=config.get("enabled") is True,
            daily_limit=config.get("daily_limit", 20),
            activated_at=config.get("activated_at", "9999"),
            accuracy_activated_at=config.get("accuracy_activated_at"),
            roles_activated_at=config.get("roles_activated_at"),
            model=config.get("model", "gpt-4.1-2025-04-14"),
        )

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            c.execute("BEGIN IMMEDIATE")
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def event(self, c, rid, kind, attempt, payload=None):
        c.execute(
            "INSERT INTO events(request_id,at,kind,attempt,payload) VALUES(?,?,?,?,?)",
            (rid, now(), kind, attempt, pack(payload or {})),
        )

    def enqueue(self, item_id, mode="auto", *, execution_key=None):
        # Read and pin a complete basket revision under its existing write lock.
        with error_basket.LOCK:
            record = error_basket.detail(self.basket, item_id)
        if not record["active"]:
            return None
        if execution_key is not None:
            if not isinstance(execution_key, str) or not 1 <= len(execution_key) <= 200:
                raise ValueError("INVALID_EXECUTION_KEY")
            record = {**record, "execution_key": execution_key}
        config = self.agent_settings()
        if mode == "auto":
            mode = (
                "agents"
                if config["enabled"]
                and max(e["at"] for e in record["history"]) >= config["activated_at"]
                else "local"
            )
            if (
                mode == "agents"
                and config.get("accuracy_activated_at")
                and max(e["at"] for e in record["history"]) >= config["accuracy_activated_at"]
            ):
                mode = "agents_v2"
            if (
                mode.startswith("agents")
                and config.get("roles_activated_at")
                and max(e["at"] for e in record["history"]) >= config["roles_activated_at"]
            ):
                mode = "agents_roles"
        if mode not in ("local", "agents", "agents_v2", "agents_roles"):
            raise HTTPException(422, "지원하지 않는 검토 방식입니다.")
        if mode.startswith("agents") and not config["enabled"]:
            raise HTTPException(409, "모델 검토가 꺼져 있습니다.")
        payload = pack(record)
        version = (
            "review-decision-loop/2-rule-gated"
            if mode == "agents_roles"
            else source_integrity.VERSION
            if mode == "local"
            else "review-agents/2.1"
            if mode == "agents_v2"
            else "review-agents/1"
        )
        revision = hashlib.sha256((version + payload).encode()).hexdigest()
        with self.db() as c:
            existing = c.execute("SELECT id FROM requests WHERE revision=?", (revision,)).fetchone()
            if existing:
                return existing["id"]
            # Upgrading routing alone must not replay paid legacy basket requests.
            if mode == "agents_roles" and execution_key is None:
                legacy_revision = hashlib.sha256(("review-decision-loop/1" + payload).encode()).hexdigest()
                legacy = c.execute("SELECT id FROM requests WHERE revision=?", (legacy_revision,)).fetchone()
                if legacy:
                    return legacy["id"]
            # Pin execution model only after deduplication; a settings change must not replay old baskets.
            if mode == "agents_roles":
                payload = pack({**record, "review_model": config["model"], "review_workflow": "rule-gated/1"})
            rid = uuid.uuid4().hex
            stamp = now()
            c.execute(
                "INSERT INTO requests(id,item_id,revision,payload,status,stage,created_at,updated_at,mode) VALUES(?,?,?,?,?,?,?,?,?)",
                (rid, item_id, revision, payload, "queued", "queued", stamp, stamp, mode),
            )
            self.event(c, rid, "queued", 0, {"revision": revision})
        self.wake.set()
        return rid

    def reconcile(self):
        # Existing basket events act as an outbox if enqueue failed after saving feedback.
        errors = []
        for p in sorted(self.basket.glob("*/snapshot.json")):
            if not any(p.parent.glob("event-*.json")):
                continue
            try:
                self.enqueue(p.parent.name)
            except Exception:
                errors.append(p.parent.name)
        self.sync_error = (
            {"item_ids": errors, "message": "일부 저장 사례의 큐 등록을 재시도하고 있습니다."}
            if errors
            else None
        )

    def recover(self):
        with self.db() as c:
            for r in c.execute("SELECT * FROM requests WHERE status='running'").fetchall():
                self.event(c, r["id"], "interrupted", r["attempt"], {"reason": "server_restarted"})
                c.execute(
                    "UPDATE requests SET status='queued',stage='queued',updated_at=? WHERE id=?",
                    (now(), r["id"]),
                )

    def overview(self):
        with self.db() as c:
            paused = (
                c.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0] == "true"
            )
            rows = c.execute("SELECT * FROM requests ORDER BY created_at DESC").fetchall()
            decisions = {
                r["request_id"]: dict(
                    child_id=r["child_id"], at=r["at"], **json.loads(r["payload"])
                )
                for r in c.execute("SELECT * FROM decisions")
            }
            confirmations = {}
            for r in c.execute("SELECT * FROM confirmations ORDER BY seq"):
                confirmations[(r["request_id"], r["scope_key"])] = dict(
                    at=r["at"], **json.loads(r["payload"])
                )
        items = []
        for r in rows:
            p = json.loads(r["payload"])
            s = p["snapshot"]
            items.append(
                dict(
                    id=r["id"],
                    item_id=r["item_id"],
                    revision=r["revision"],
                    status=r["status"],
                    stage=r["stage"],
                    attempt=r["attempt"],
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                    mode=r["mode"],
                    parent_id=r["parent_id"],
                    decision=decisions.get(r["id"]),
                    model=p.get("review_model", "gpt-4.1-2025-04-14")
                    if r["mode"].startswith("agents")
                    else None,
                    submitted_opinion=p.get("review_decision"),
                    confirmations=[
                        v
                        for (rid, _), v in confirmations.items()
                        if rid == r["id"] and v["confirmed"]
                    ],
                    confirmation=next(
                        (
                            v
                            for (rid, _), v in confirmations.items()
                            if rid == r["id"] and v["confirmed"] and not v["source_ids"]
                        ),
                        None,
                    ),
                    job_id=s["job_id"],
                    document_name=s["document_name"],
                    selection=s["selection"],
                    feedback=p["feedback"],
                    reference_revision=(p.get("reference") or {}).get("revision"),
                    source_pdf_sha256=s["source_pdf_sha256"],
                    canonical_sha256=s["canonical_sha256"],
                    result=json.loads(r["result"]) if r["result"] else None,
                    error=r["error"],
                )
            )
        latest = {}
        for i in items:
            latest.setdefault(i["item_id"], i)
        alerts = [
            dict(
                request_id=i["id"],
                item_id=i["item_id"],
                document_name=i["document_name"],
                page=i["selection"]["page"],
                reason=i["error"] if i["status"] == "failed" else i["result"].get("verdict"),
            )
            for i in latest.values()
            if not i["decision"]
            and not i["confirmation"]
            and (
                i["status"] == "failed"
                or (
                    i["status"] == "completed"
                    and i["result"]
                    and i["result"].get("verdict") != "no_structural_issue_detected"
                )
            )
        ]
        return dict(
            paused=paused,
            items=items,
            alerts=alerts,
            sync_error=self.sync_error,
            checker=source_integrity.VERSION,
            worker_alive=bool(self.thread and self.thread.is_alive()),
            agents=self.agent_settings(),
        )

    def decide(self, rid, decision):
        """Append a human claim and exactly one child review atomically; never revise old output."""
        with self.db() as c:
            r = c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
            if not r:
                raise HTTPException(404, "검토 요청을 찾지 못했습니다.")
            previous = c.execute("SELECT * FROM decisions WHERE request_id=?", (rid,)).fetchone()
            if previous:
                if previous["payload"] != pack(decision):
                    raise HTTPException(
                        409, "이미 판단을 저장했습니다. 재검토 결과에서 새 의견을 남겨주세요."
                    )
                return previous["child_id"]
            confirmation = c.execute(
                "SELECT payload FROM confirmations WHERE request_id=? AND scope_key=? ORDER BY seq DESC LIMIT 1",
                (rid, pack([])),
            ).fetchone()
            if confirmation and json.loads(confirmation["payload"])["confirmed"]:
                raise HTTPException(
                    409, "이미 확인 완료한 범위입니다. 확인을 취소한 뒤 재검토해주세요."
                )
            if r["status"] not in ("completed", "failed"):
                raise HTTPException(409, "검토가 끝난 뒤 판단을 저장해주세요.")
            latest = c.execute(
                "SELECT id FROM requests WHERE item_id=? ORDER BY created_at DESC LIMIT 1",
                (r["item_id"],),
            ).fetchone()
            if latest["id"] != rid:
                raise HTTPException(409, "더 최근 검토가 있습니다. 최신 결과에서 판단해주세요.")
            payload = json.loads(r["payload"])
            allowed = set()
            for t in payload["snapshot"].get("physical_tables", []):
                allowed.add(t["table_id"])
                allowed.update(cell["cell_id"] for cell in t["cells"])
            allowed.update(
                t["cell_id"]
                for t in (json.loads(r["result"]) if r["result"] else {})
                .get("interpretation", {})
                .get("targets", [])
            )
            if set(decision.get("source_ids", [])) - allowed:
                raise HTTPException(422, "이 요청에 없는 셀·표 ID입니다. 원본에서 확인해주세요.")
            if (
                decision["choice"] not in ("agree", "disagree", "uncertain")
                or not decision["note"].strip()
            ):
                raise HTTPException(422, "판단과 원본에서 확인한 이유를 입력해주세요.")
            if not self.agent_settings()["enabled"]:
                raise HTTPException(
                    409,
                    "자동 모델 검토를 켠 뒤 재검토를 요청해주세요. 입력한 의견은 화면에 남아 있습니다.",
                )
            child = uuid.uuid4().hex
            stamp = now()
            payload["review_decision"] = {
                **decision,
                "parent_request_id": rid,
                "status": "human_claim_not_gold",
            }
            payload["review_model"] = self.agent_settings()["model"]
            payload["review_workflow"] = "rule-gated/1"
            revision = hashlib.sha256(
                ("review-decision-loop/1" + rid + pack(decision)).encode()
            ).hexdigest()
            c.execute(
                "INSERT INTO requests(id,item_id,revision,payload,status,stage,created_at,updated_at,mode,parent_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    child,
                    r["item_id"],
                    revision,
                    pack(payload),
                    "queued",
                    "queued",
                    stamp,
                    stamp,
                    "agents_roles",
                    rid,
                ),
            )
            c.execute("INSERT INTO decisions VALUES(?,?,?,?)", (rid, child, stamp, pack(decision)))
            self.event(c, rid, "human_decision", r["attempt"], dict(**decision, child_id=child))
            self.event(c, child, "queued", 0, dict(parent_id=rid, reason="human_decision"))
        self.wake.set()
        return child

    def confirm(self, rid, confirmation):
        """Record source-display review only; no model call, child run, or IR publication."""
        ids = sorted(set(confirmation.get("source_ids", [])))
        value = {
            **confirmation,
            "source_ids": ids,
            "scope": "selected_cells" if ids else "original_request_selection",
            "source": "human",
            "applies_to": "saved_source_display",
            "model_candidate_applied": False,
        }
        if (
            not value.get("note", "").strip()
            or len(value["note"]) > 4000
            or len(ids) > 24
            or type(value.get("confirmed")) is not bool
        ):
            raise HTTPException(422, "확인 범위와 기록할 내용을 확인해주세요.")
        with self.db() as c:
            r = c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
            if not r:
                raise HTTPException(404, "검토 요청을 찾지 못했습니다.")
            if r["status"] not in ("completed", "failed"):
                raise HTTPException(409, "검토 실행이 끝난 뒤 확인해주세요.")
            latest = c.execute(
                "SELECT id FROM requests WHERE item_id=? ORDER BY created_at DESC LIMIT 1",
                (r["item_id"],),
            ).fetchone()
            if (
                latest["id"] != rid
                or c.execute("SELECT 1 FROM decisions WHERE request_id=?", (rid,)).fetchone()
            ):
                raise HTTPException(409, "더 최근 재검토가 있습니다. 최신 결과에서 확인해주세요.")
            p = json.loads(r["payload"])
            allowed = set()
            for t in p["snapshot"].get("physical_tables", []):
                allowed.add(t["table_id"])
                allowed.update(cell["cell_id"] for cell in t["cells"])
            allowed.update(
                t["cell_id"]
                for t in (json.loads(r["result"]) if r["result"] else {})
                .get("interpretation", {})
                .get("targets", [])
            )
            if set(ids) - allowed:
                raise HTTPException(422, "이 요청에 없는 셀·표 ID입니다.")
            scope = pack(ids)
            previous = c.execute(
                "SELECT payload FROM confirmations WHERE request_id=? AND scope_key=? ORDER BY seq DESC LIMIT 1",
                (rid, scope),
            ).fetchone()
            if previous and previous["payload"] == pack(value):
                return rid
            c.execute(
                "INSERT INTO confirmations(request_id,scope_key,at,payload) VALUES(?,?,?,?)",
                (rid, scope, now(), pack(value)),
            )
            self.event(c, rid, "human_confirmation", r["attempt"], value)
        return rid

    def detail(self, rid):
        item = next((i for i in self.overview()["items"] if i["id"] == rid), None)
        if not item:
            raise HTTPException(404, "검토 요청을 찾지 못했습니다.")
        with self.db() as c:
            item["history"] = [
                dict(
                    seq=r["seq"],
                    at=r["at"],
                    kind=r["kind"],
                    attempt=r["attempt"],
                    payload=json.loads(r["payload"]),
                )
                for r in c.execute("SELECT * FROM events WHERE request_id=? ORDER BY seq", (rid,))
            ]
        return item

    def pause(self, value):
        with self.db() as c:
            c.execute(
                "UPDATE settings SET value=? WHERE key='paused'", ("true" if value else "false",)
            )
            self.event(c, "queue", "paused" if value else "resumed", 0)
        self.wake.set()

    def action(self, rid, action):
        with self.db() as c:
            r = c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
            if not r:
                raise HTTPException(404, "검토 요청을 찾지 못했습니다.")
            if action == "cancel":
                if r["status"] not in ("queued", "running"):
                    raise HTTPException(409, "이미 종료된 요청입니다.")
                c.execute(
                    "UPDATE requests SET status='cancelled',stage='cancelled',updated_at=? WHERE id=?",
                    (now(), rid),
                )
            elif action == "retry":
                if r["status"] not in ("failed", "cancelled"):
                    raise HTTPException(409, "실패·취소된 요청만 재시도할 수 있습니다.")
                c.execute(
                    "UPDATE requests SET status='queued',stage='queued',result=NULL,error=NULL,updated_at=? WHERE id=?",
                    (now(), rid),
                )
            else:
                raise HTTPException(422, "지원하지 않는 동작입니다.")
            self.event(c, rid, action, r["attempt"])
        self.wake.set()

    def progress(self, rid, attempt, stage):
        with self.db() as c:
            changed = c.execute(
                "UPDATE requests SET stage=?,updated_at=? WHERE id=? AND status='running' AND attempt=?",
                (stage, now(), rid, attempt),
            ).rowcount
            if not changed:
                raise InterruptedError("REVIEW_CANCELLED")
            self.event(c, rid, "stage", attempt, {"stage": stage})

    def run_one(self, request_id=None):
        with self.db() as c:
            if c.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0] == "true":
                return False
            r = c.execute(
                "SELECT * FROM requests WHERE status='queued' AND (? IS NULL OR id=?) ORDER BY created_at LIMIT 1",
                (request_id, request_id),
            ).fetchone()
            if not r:
                return False
            rid = r["id"]
            attempt = r["attempt"] + 1
            c.execute(
                "UPDATE requests SET status='running',stage='source_integrity',attempt=?,updated_at=? WHERE id=?",
                (attempt, now(), rid),
            )
            self.event(c, rid, "started", attempt)
        try:

            def progress(stage):
                return self.progress(rid, attempt, stage)

            if r["mode"].startswith("agents"):
                if self.agent_checker is None:
                    from doc_understanding.agents.workflows.registry import get_executor

                    checker = get_executor(r["mode"])
                else:
                    checker = self.agent_checker
                output = checker(json.loads(r["payload"]), self.data, progress, rid)
            else:
                output = self.checker(json.loads(r["payload"]), self.data, progress)
            with self.db() as c:
                if r["parent_id"]:
                    parent = c.execute(
                        "SELECT result FROM requests WHERE id=?", (r["parent_id"],)
                    ).fetchone()
                    from doc_understanding.agents.workflows.table_review.service import compare

                    output["comparison"] = compare(
                        json.loads(parent["result"]) if parent and parent["result"] else {}, output
                    )
                changed = c.execute(
                    "UPDATE requests SET status='completed',stage='report_ready',result=?,updated_at=? WHERE id=? AND status='running' AND attempt=?",
                    (pack(output), now(), rid, attempt),
                ).rowcount
                if changed:
                    self.event(c, rid, "completed", attempt, output)
        except InterruptedError:
            pass
        except Exception as exc:
            with self.db() as c:
                # Exception messages may contain source data; keep only the error class.
                changed = c.execute(
                    "UPDATE requests SET status='failed',stage='failed',error=?,updated_at=? WHERE id=? AND status='running' AND attempt=?",
                    (type(exc).__name__, now(), rid, attempt),
                ).rowcount
                if changed:
                    self.event(c, rid, "failed", attempt, {"error": type(exc).__name__})
        return True

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop.clear()
        self.recover()
        self.reconcile()

        def loop():
            while not self.stop.is_set():
                try:
                    if self.run_one():
                        continue
                    self.wake.wait(3)
                    self.wake.clear()
                    self.reconcile()
                except Exception:
                    self.sync_error = {
                        "message": "검토 큐 처리 오류입니다. 기록을 보존하고 재시도합니다."
                    }
                    self.stop.wait(3)

        self.thread = threading.Thread(target=loop, name="local-review-queue", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=5)
