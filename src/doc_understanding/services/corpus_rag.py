# 역할: 적재된 모든 문서를 하나의 검색 코퍼스로 다룬다. 일괄 적재(중복 임베딩 없음)와 코퍼스 전체 질문을 실행한다.
"""Corpus RAG over stored documents.

- 코퍼스: 문서(PDF 해시)마다 최신 완료 실행 하나. 현재 색인 버전이면 검색 대상, 아니면 재색인 필요로 표시한다.
  같은 문서를 여러 번 적재해도 검색 결과가 중복되지 않는다.
- 문서 목록·검색 범위는 문서 목록표(rag/catalog.py)에서 읽는다. 목록 조회나 질문 때 문서 저장본을 열지 않고,
  전체 검색은 실행 ID를 나열하지 않는다(CorpusScope).
- 적재: 완료된 웹 문서 작업을 차례로 구조 검토 → DB 적재·색인 → 임베딩한다. 같은 프로필·같은 입력 원문의
  벡터는 다른 실행에서 복사하므로 다시 임베딩하지 않는다(embed_run).
- 질문: 코퍼스 실행 전체(또는 선택한 일부)에서 기존 검색·근거·답변·질문 이해 에이전트를 그대로 쓴다.
웹 RAG 실행기(WebRagService)의 잠금·작업 큐·저장을 공유한다. 적재와 질문은 서로 다른 줄에서 실행하므로
적재 중에도 이미 준비된 문서에 질문할 수 있다. 같은 줄(적재끼리, 질문끼리)은 한 번에 하나만 실행한다.
"""
import json
import os
import re

from pydantic import Field

from doc_understanding.rag import catalog as document_catalog
from doc_understanding.rag.indexing.writer import INDEX_VERSION
from doc_understanding.rag.retrieval.scope import CorpusScope
from doc_understanding.services.web_rag import (
    PrepareRequest,
    QuestionRequest,
    WebRagError,
    embedding_audit,
    input_hash,
    read_json,
    safe_error,
)
from doc_understanding.storage.staging.local import query, query_params

CORPUS = "corpus"


class IngestRequest(PrepareRequest):
    job_ids: list[str] = Field(min_length=1, max_length=100)


class CorpusQuestionRequest(QuestionRequest):
    # 비워 두면 코퍼스 전체에서 검색한다.
    run_ids: list[str] | None = Field(default=None, max_length=200)


def latest_runs():
    """문서마다 최신 완료 실행과 요약(쪽수·검색 항목 수·구조 검토 적용 여부). 문서 목록표에서 읽는다
    (문서 저장본을 열지 않는다). 평가·점검 스크립트용이며, 질문 경로는 전체 목록을 읽지 않는다."""
    return document_catalog.current_runs()


def corpus_documents():
    """현재 색인 버전의 문서마다 최신 실행의 CanonicalDocument(읽기 전용). document_id → 문서."""
    from doc_understanding.rag import repository
    runs = [r for r in latest_runs() if r["index_version"] == INDEX_VERSION]
    return {r["document_id"]: repository.fetch_document(r["run_id"]) for r in runs}


def embedding_profiles(run_ids):
    if not run_ids:
        return []
    return query_params("""SELECT run_id, profile->>'provider' AS provider, profile->>'model' AS model,
        dimensions, profile_id, count(*) AS vectors FROM rag.entry_embedding
      WHERE run_id = ANY(%s) GROUP BY run_id, provider, model, dimensions, profile_id""", [list(run_ids)])


def run_documents():
    return {r["run_id"]: r["document_id"] for r in query("SELECT run_id, document_id FROM rag.document_snapshot")}


class CorpusService:
    def __init__(self, web):
        self.web = web

    def _job_runs(self):
        """웹 문서 작업 → 그 작업이 적재한 실행(stored.json)."""
        out = {}
        for path in self.web.output.glob("*/stored.json"):
            if re.fullmatch(r"[a-f0-9]{32}", path.parent.name):
                stored = read_json(path)
                out[path.parent.name] = dict(run_id=stored.get("run_id"), index_version=stored.get("index_version"),
                                             reviewed=bool(stored.get("reviewed_document")))
        return out

    def catalog(self, jobs):
        """코퍼스 규모 요약과, 완료된 문서 작업의 적재 상태. 문서를 전부 나열하지 않는다(목록은 documents())."""
        job_runs = self._job_runs()
        # 화면에서 올린 문서는 업로드한 파일 이름으로 찾을 수 있게 표시 이름을 목록표에 적어 둔다.
        document_catalog.set_names({v["run_id"]: jobs.get(jid, {}).get("name") for jid, v in job_runs.items()})
        rows = document_catalog.lookup([v["run_id"] for v in job_runs.values()])
        current = document_catalog.current_for_documents([r["document_id"] for r in rows.values()], INDEX_VERSION)
        candidates = []
        for jid, job in jobs.items():
            if job.get("status") != "done":
                continue
            stored = job_runs.get(jid)
            document = rows.get(stored["run_id"], {}).get("document_id") if stored else None
            if document in current:
                state = "in_corpus"
            elif stored:
                state = "stale" if stored["index_version"] != INDEX_VERSION else "stored_not_latest"
            else:
                state = "not_ingested"
            candidates.append(dict(job_id=jid, name=job.get("name"), pages=job.get("pages"), state=state,
                                   corpus_run_id=current.get(document)))
        return dict(index_version=INDEX_VERSION, stats=document_catalog.stats(INDEX_VERSION), jobs=candidates)

    def documents(self, jobs, text=None, limit=20, offset=0, stale=False):
        """코퍼스 문서를 이름으로 찾아 한 쪽 분량만 돌려준다(질문 범위를 좁히는 선택적 필터용)."""
        found = document_catalog.find(INDEX_VERSION, text, limit, offset, stale)
        profiles = {}
        for p in embedding_profiles([r["run_id"] for r in found["documents"]]):
            profiles.setdefault(p["run_id"], []).append(p)
        job_of = {v["run_id"]: jid for jid, v in self._job_runs().items()}
        found["documents"] = [dict(r, job_id=job_of.get(r["run_id"]), embeddings=profiles.get(r["run_id"], []))
                              for r in found["documents"]]
        return found

    def state(self):
        with self.web.lock:
            result = dict(**self.web.busy(),
                          api_key_available=bool(os.environ.get("OPENAI_API_KEY")),
                          index_version=INDEX_VERSION)
            for action in ("ingest", "question"):
                path = self.web.output / CORPUS / ("latest-" + action + ".json")
                result[action] = read_json(path) if path.exists() else None
            return result

    def task(self, request_id):
        """요청 ID로 코퍼스 작업 하나의 기록을 읽는다(화면이 자기가 보낸 질문·적재만 따라가게 한다)."""
        if not re.fullmatch(r"[a-f0-9]{32}", request_id or ""):
            raise WebRagError("TASK_NOT_FOUND")
        path = self.web.output / CORPUS / "tasks" / (request_id + ".json")
        with self.web.lock:
            if not path.is_file():
                raise WebRagError("TASK_NOT_FOUND")
            return read_json(path)

    def start(self, action, request, jobs):
        if action not in ("ingest", "question"):
            raise WebRagError("INVALID_CORPUS_ACTION")
        data = request.model_dump(mode="json")
        signature = input_hash(json.dumps([CORPUS, action, data], sort_keys=True))
        path = self.web.output / CORPUS / "tasks" / (request.request_id + ".json")
        with self.web.lock:
            if path.exists():
                task = read_json(path)
                if task["signature"] != signature:
                    raise WebRagError("REQUEST_ID_CONFLICT")
                return task
            if self.web.lanes.get(action):
                raise WebRagError("RAG_BUSY")
            self.web._check("prepare" if action == "ingest" else "question", request)
            if action == "ingest":
                for jid in request.job_ids:
                    if jobs.get(jid, {}).get("status") != "done":
                        raise WebRagError("DOCUMENT_NOT_READY")
                    self.web.manifest(jid, self.web.document(jid)[0])
            task = dict(job_id=CORPUS, request_id=request.request_id, action=action, signature=signature,
                        index_version=INDEX_VERSION, config=data, status="queued", stage="queued")
            self.web._save(task)
            self.web.claim(action, request.request_id)
            try:
                self.web.pool.submit(self._execute, task, request)
            except Exception:
                self.web.release(action)
                task.update(status="failed", error="WORKER_START_FAILED")
                self.web._save(task)
                raise WebRagError("WORKER_START_FAILED") from None
            return dict(task)

    def _execute(self, task, request):
        clients = []
        try:
            # 첫 단계: 적재는 구조 검토, 질문은 질문 이해(에이전트) 또는 검색. 이후 단계는 실행하면서 알린다.
            first = "reviewing" if task["action"] == "ingest" else "planning" if request.agent else "searching"
            task.update(status="running", stage=first)
            self.web._save(task)
            if task["action"] == "ingest":
                self._ingest(task, request, clients)
            else:
                self._question(task, request, clients)
            task["stage"] = "finished"
        except Exception as error:
            task.update(status="failed", error=safe_error(error))
        finally:
            if clients:
                task["embedding_audit"] = embedding_audit(clients)
            try:
                self.web._save(task)
            finally:
                self.web.release(task["action"])

    def _ingest(self, task, request, clients):
        """문서마다 독립적으로 적재한다. 한 문서가 실패해도 나머지는 계속하며 결과를 문서별로 남긴다."""
        items = task.setdefault("items", [])
        for jid in request.job_ids:
            item = dict(job_id=jid, request_id=request.request_id, status="running", stage="queued")
            items.append(item)
            self.web._save(task)
            try:
                doc, digest = self.web.document(jid)
                self.web._prepare_job(item, jid, doc, digest, request, lambda: self.web._save(task), clients)
            except Exception as error:
                item.update(status="failed", error=safe_error(error))
            self.web._save(task)
        failed = sum(i["status"] == "failed" for i in items)
        task["status"] = "completed" if not failed else ("partial" if failed < len(items) else "failed")

    def _question(self, task, request, clients):
        if request.run_ids:
            # 선택적 필터: 고른 문서만. 현재 색인 버전의 검색 대상 실행이어야 한다.
            rows = document_catalog.lookup(request.run_ids)
            ids = list(dict.fromkeys(request.run_ids))
            if any(not rows.get(r, {}).get("is_current") or rows[r]["index_version"] != INDEX_VERSION for r in ids):
                raise WebRagError("RUN_NOT_IN_CORPUS")
            scope, task["run_ids"] = ids, ids
        else:
            # 기본: 코퍼스 전체. 실행 ID를 나열하지 않고 목록표 조건으로 검색 범위를 정한다.
            scope, task["run_ids"] = CorpusScope(INDEX_VERSION), None
        if not scope:
            raise WebRagError("CORPUS_EMPTY")
        task["scope"] = dict(kind="selected" if request.run_ids else "corpus", documents=len(scope),
                             index_version=INDEX_VERSION)
        try:
            # 여러 문서에서 근거를 모을 수 있게 문서 수 한도를 늘린다(최대 3).
            self.web._question(task, request, scope, clients, max_documents=min(len(scope), 3))
        finally:
            # 근거·후보에 나온 문서만 이름과 원문 보기 작업을 붙인다(코퍼스 전체 목록을 싣지 않는다).
            used = [e.get("run_id") for e in [*(task.get("evidence") or []), *(task.get("hits") or [])]]
            job_of = {v["run_id"]: jid for jid, v in self._job_runs().items()}
            task["documents"] = {rid: dict(name=row["name"], job_id=job_of.get(rid))
                                 for rid, row in document_catalog.lookup(used).items()}
        task["status"] = "completed"
