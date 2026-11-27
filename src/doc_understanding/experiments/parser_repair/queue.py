# 역할: 파서 수정 제안·격리 실행·재추출 큐를 관리한다. 정답 비교는 evaluation.parser에 위임한다.
"""One bounded code experiment per feedback revision, independent of model review retries."""

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from doc_understanding.experiments.parser_repair import engine


def now():
    return datetime.now(timezone.utc).isoformat()


class RepairQueue:
    def __init__(self, data, reviews, executor=None):
        self.data = Path(data)
        self.reviews = reviews
        self.executor = executor
        self.folder = self.data / "parser-repairs"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / "queue.sqlite3"
        self.stop = threading.Event()
        self.thread = None
        self.sync_error = None
        with self.db() as c:
            c.executescript("""CREATE TABLE IF NOT EXISTS repairs(
              id TEXT PRIMARY KEY, review_id TEXT UNIQUE, status TEXT, stage TEXT,
              created_at TEXT, updated_at TEXT, result TEXT, error TEXT);
              CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, repair_id TEXT, at TEXT, stage TEXT, message TEXT);
              CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);""")
            if "attempt" not in {r["name"] for r in c.execute("PRAGMA table_info(repairs)")}:
                c.execute("ALTER TABLE repairs ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0")

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

    def config(self):
        with self.db() as c:
            r = c.execute("SELECT value FROM settings WHERE key='config'").fetchone()
        cfg = json.loads(r[0]) if r else dict(enabled=False, activated_at=now(), daily_limit=2)
        cfg.setdefault("mode", "local_refresh")
        return cfg

    def enable(self, enabled, mode=None):
        cfg = self.config()
        if mode is not None:
            if mode not in ("local_refresh", "code_patch", "automatic"):
                raise ValueError("INVALID_REPAIR_MODE")
            cfg["mode"] = mode
        if enabled and not cfg["enabled"]:
            cfg["activated_at"] = now()
        cfg["enabled"] = enabled
        with self.db() as c:
            c.execute("INSERT OR REPLACE INTO settings VALUES('config',?)", (json.dumps(cfg),))

    def enqueue(self, rid):
        # Pin the immutable review payload now, not the basket's mutable latest revision.
        with self.reviews.db() as c:
            r = c.execute("SELECT payload FROM requests WHERE id=?", (rid,)).fetchone()
        if not r:
            raise ValueError("REVIEW_NOT_FOUND")
        with self.db() as c:
            previous = c.execute("SELECT id FROM repairs WHERE review_id=?", (rid,)).fetchone()
            if previous:
                return previous[0]
            ident = uuid.uuid4().hex
            stamp = now()
            folder = self.folder / ident
            folder.mkdir()
            engine.save(folder / "request.json", json.loads(r[0]))
            c.execute(
                "INSERT INTO repairs(id,review_id,status,stage,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (ident, rid, "queued", "queued", stamp, stamp),
            )
            c.execute(
                "INSERT INTO events(repair_id,at,stage,message) VALUES(?,?,?,?)",
                (ident, stamp, "queued", "의견을 파서 재실행·개선 실험으로 접수했습니다."),
            )
        return ident

    def retry(self, ident):
        with self.db() as c:
            row = c.execute("SELECT * FROM repairs WHERE id=?", (ident,)).fetchone()
            if not row or row["status"] not in (
                "failed",
                "interrupted",
                "validation_failed",
                "awaiting_source_review",
                "needs_source_reference",
                "no_improvement",
            ):
                raise ValueError("RETRY_NOT_ALLOWED")
            c.execute(
                "UPDATE repairs SET status='queued',stage='queued',error=NULL,updated_at=? WHERE id=?",
                (now(), ident),
            )
            c.execute(
                "INSERT INTO events(repair_id,at,stage,message) VALUES(?,?,?,?)",
                (ident, now(), "retry", "이전 실패·파일은 보존하고 새 실행 폴더에서 재시도합니다."),
            )

    def reconcile(self):
        cfg = self.config()
        if not cfg["enabled"]:
            return
        overview = self.reviews.overview()
        latest = {}
        for item in overview["items"]:
            latest.setdefault(item["item_id"], item)
        for item in latest.values():
            allowed=("queued","running","completed","failed") if cfg['mode']=='automatic' else ("completed","failed")
            if item["created_at"] < cfg["activated_at"] or item["status"] not in allowed:
                continue
            if item["confirmation"] or item["decision"]:
                continue
            if item.get("submitted_opinion", {}):
                if item["submitted_opinion"]["choice"] != "disagree":
                    continue
            self.enqueue(item["id"])

    def progress(self, ident, stage, message):
        with self.db() as c:
            c.execute("UPDATE repairs SET stage=?,updated_at=? WHERE id=?", (stage, now(), ident))
            c.execute(
                "INSERT INTO events(repair_id,at,stage,message) VALUES(?,?,?,?)",
                (ident, now(), stage, message),
            )
            if stage=='model_call':
                c.execute("INSERT INTO events(repair_id,at,stage,message) VALUES(?,?,?,?)",
                          (ident,now(),'paid_started','수정안 모델 호출 1회를 시작합니다.'))

    def run_one(self, *, explicit_extra_attempt=False):
        cfg = self.config()
        if not cfg["enabled"]:
            return False
        midnight = (
            datetime.now(timezone(timedelta(hours=9)))
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .astimezone(timezone.utc)
            .isoformat()
        )
        with self.db() as c:
            count = c.execute(
                "SELECT count(*) FROM events WHERE stage=? AND at>=?", ('model_call' if cfg['mode']=='automatic' else 'paid_started',midnight)
            ).fetchone()[0]
            if (
                cfg["mode"] in ("code_patch", "automatic")
                and count >= cfg["daily_limit"]
                and not explicit_extra_attempt
            ):
                waiting=c.execute("SELECT id,stage FROM repairs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
                if waiting and waiting['stage']!='budget_wait':
                    c.execute("UPDATE repairs SET stage='budget_wait',updated_at=? WHERE id=?",(now(),waiting['id']))
                    c.execute("INSERT INTO events(repair_id,at,stage,message) VALUES(?,?,?,?)",
                              (waiting['id'],now(),'budget_wait','오늘 수정안 생성 한도에 도달했습니다. 요청을 보존하고 다음 날 한도 회복을 기다립니다.'))
                return False
            r = c.execute(
                "SELECT * FROM repairs WHERE status='queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if not r:
                return False
            ident = r["id"]
            attempt = r["attempt"] + 1
            c.execute("UPDATE repairs SET status='running',attempt=? WHERE id=?", (attempt, ident))
            c.execute(
                "INSERT INTO events(repair_id,at,stage,message) VALUES(?,?,?,?)",
                (ident, now(), "started", "격리된 파서 개선 실험을 시작합니다."),
            )
            if cfg["mode"] == "code_patch":
                c.execute(
                    "INSERT INTO events(repair_id,at,stage,message) VALUES(?,?,?,?)",
                    (
                        ident,
                        now(),
                        "paid_started",
                        "유료 코드 생성 시도 1회를 사용합니다. 재시도도 한도에 포함합니다.",
                    ),
                )
        try:
            folder = self.folder / ident / ("attempt-" + str(attempt))
            folder.mkdir()
            from doc_understanding.experiments.parser_repair.parser_refresh import execute as refresh
            plan_file = self.folder/'plans'/(r['review_id']+'.json')
            plan = engine.read(plan_file) if plan_file.exists() and self.executor is None else None
            if plan is not None:
                from doc_understanding.experiments.parser_repair.registered_plan import execute as run_plan
                executor = lambda record,data,out,progress:run_plan(record,data,out,progress,plan)
            else:
                from doc_understanding.experiments.parser_repair.automatic import execute as automatic
                executor = self.executor or {'local_refresh':refresh,'automatic':automatic,'code_patch':engine.execute}[cfg['mode']]
            engine.save(folder / "execution-mode.json", dict(mode='registered_patch' if plan else cfg["mode"]))
            result = executor(
                engine.read(self.folder / ident / "request.json"),
                self.data,
                folder,
                lambda s, m: self.progress(ident, s, m),
            )
            with self.db() as c:
                c.execute(
                    "UPDATE repairs SET status=?,result=?,updated_at=? WHERE id=?",
                    (result["status"], json.dumps(result, ensure_ascii=False), now(), ident),
                )
        except Exception as exc:
            error=(type(exc).__name__+': '+str(exc)[:160]) if isinstance(exc,(ValueError,RuntimeError)) else type(exc).__name__
            # Keep completed comparisons and call accounting even if a later stage fails.
            def saved(name,default):
                file=folder/name
                return engine.read(file) if file.exists() else default
            proposal=saved('proposal.json',{})
            result=dict(status='failed',mode=cfg['mode'],diagnosis='실행을 완료하지 못했습니다.',
                        limits=error,error=error,datasets=saved('results.json',[]),tests=saved('tests.json',{}),
                        changed_files=[dict(path=e['path'],reason=e['reason']) for e in proposal.get('edits',[])],
                        candidate_modified=(folder/'candidate').exists(),stable_parser_modified=False,
                        usage=saved('usage.json',{}),next_action='실패 이유와 보존된 전후 결과를 확인해 주세요.')
            engine.save(folder/'result.json',result)
            with self.db() as c:
                c.execute(
                    "UPDATE repairs SET status='failed',error=?,result=?,updated_at=? WHERE id=?",
                    (
                        error,json.dumps(result,ensure_ascii=False),
                        now(),
                        ident,
                    ),
                )
            self.progress(ident, "failed", "실험이 중단됐습니다. 수정안과 이전 결과는 보존됩니다.")
        return True

    def overview(self):
        with self.db() as c:
            items = [dict(r) for r in c.execute("SELECT * FROM repairs ORDER BY created_at DESC")]
            for item in items:
                item["result"] = json.loads(item["result"]) if item["result"] else None
                item["events"] = [
                    dict(e)
                    for e in c.execute(
                        "SELECT * FROM events WHERE repair_id=? ORDER BY seq", (item["id"],)
                    )
                ]
                folder = self.folder / item["id"]
                request=engine.read(folder/'request.json')
                snapshot=request.get('snapshot',{})
                item['document_name']=snapshot.get('document_name','문서')
                item['page']=snapshot.get('selection',{}).get('page')
                item['feedback']=request.get('feedback',{}).get('note','')
                costs = []
                history = []
                attempts = ([folder] if (folder / "baseline").exists() else []) + sorted(
                    folder.glob("attempt-*")
                )
                for attempt in attempts:
                    report = attempt / "result.json"
                    if report.exists():
                        saved = engine.read(report)
                        history.append(
                            dict(
                                attempt=attempt.name,
                                status=saved["status"],
                                mode=saved.get("mode", "code_patch"),
                                diagnosis=saved.get("diagnosis",''),
                                datasets=saved.get("datasets",[]),
                            )
                        )
                    response = attempt / "claude-response.json"
                    raw = engine.read(response) if response.exists() else {}
                    mode_file = attempt / "execution-mode.json"
                    local = (
                        mode_file.exists() and engine.read(mode_file).get("mode") in ("local_refresh", "registered_patch")
                    )
                    costs.append(
                        dict(
                            attempt=attempt.name if attempt != folder else "initial",
                            estimated_cost_usd=0 if local else raw.get("total_cost_usd"),
                            status="모델 호출 없음 · 로컬 재추출"
                            if local
                            else raw.get("subtype", "기록 없음 또는 진행 중"),
                            api_turns=raw.get("num_turns"),
                            models=raw.get("modelUsage", {}),
                        )
                    )
                    if (attempt/'patch.json').exists():
                        model=engine.read(attempt/'patch.json')
                        costs[-1].update(estimated_cost_usd=model.get('estimated_cost_usd'),
                                         status=model.get('status'),api_turns=model.get('api_calls'),models={model.get('model','GPT-4.1 mini'):model.get('usage')})
                    elif mode_file.exists() and engine.read(mode_file).get('mode')=='automatic' and not (attempt/'patch-started.json').exists():
                        costs[-1].update(estimated_cost_usd=0,status='모델 호출 전 · 비용 없음',api_turns=0)
                item["costs"] = costs
                item["attempt_history"] = history
        return dict(
            config=self.config(),
            items=items,
            sync_error=self.sync_error,
            worker_alive=bool(self.thread and self.thread.is_alive()),
        )

    def apply_passing_result(self, ident):
        from doc_understanding.experiments.parser_repair.registered_plan import apply_verified
        # Hold queue ownership while checking and applying the exact completed attempt.
        with self.db() as c:
            row=c.execute('SELECT * FROM repairs WHERE id=?',(ident,)).fetchone()
            if not row or row['status']!='awaiting_source_review':raise ValueError('COMPLETED_RESULT_REQUIRED')
            result=json.loads(row['result'])
            if result.get('stable_parser_modified'):return result
            folder=self.folder/ident/('attempt-'+str(row['attempt']))
            plan=engine.read(folder/'registered-plan.json')
            engine.save(folder/'pre-application-result.json',result)
            apply_verified(folder,plan,result)
            engine.save(folder/'result.json',result)
            c.execute('UPDATE repairs SET result=?,updated_at=? WHERE id=?',(json.dumps(result,ensure_ascii=False),now(),ident))
            c.execute('INSERT INTO events(repair_id,at,stage,message) VALUES(?,?,?,?)',
                      (ident,now(),'apply_verified_patch','원본 성공 조건과 회귀 검사를 통과한 수정안을 로컬 코드에 반영했습니다. 원본·이전 코드는 보존했습니다.'))
        return result

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        with self.db() as c:
            c.execute(
                "UPDATE repairs SET status='interrupted',error='PROCESS_RESTARTED' WHERE status='running'"
            )
        self.stop.clear()

        def loop():
            while not self.stop.is_set():
                try:
                    self.reconcile()
                    self.run_one()
                    self.sync_error = None
                except Exception as exc:
                    self.sync_error = type(exc).__name__
                self.stop.wait(3)

        self.thread = threading.Thread(target=loop, name="parser-repair-worker", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=1)
