"""역할: 로컬 웹의 문서별 RAG 작업·진행률·재실행 기록을 기존 RAG 기능에 연결한다."""

import hashlib
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from doc_understanding.domain.pipeline_contracts import CanonicalDocument
from doc_understanding.rag import repository
from doc_understanding.rag.embedding import repository as vectors
from doc_understanding.rag.embedding.client import EmbeddingClient
from doc_understanding.rag.embedding.indexer import embed_run
from doc_understanding.rag.embedding.models import EmbeddingConfig, input_hash
from doc_understanding.rag.indexing.entries import make_entries
from doc_understanding.rag.indexing.writer import INDEX_VERSION
from doc_understanding.rag.retrieval.runtime import Retriever
from doc_understanding.rag.service import generate_answer, resolve_evidence


class PrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    embed: bool = True
    allow_external: bool = False
    embedding: EmbeddingConfig | None = None
    # 구조 검토 후보(ADR-078)를 적용한 문서로 색인한다. 기본은 규칙만 쓴다.
    structure_review: bool = True
    # 규칙이 모호로 남긴 표에만 모델(선택 + Judge)을 쓴다. 모델·단가를 모두 주고 allow_external일 때만 호출한다.
    structure_review_model: str | None = Field(default=None, min_length=1, max_length=100)
    structure_review_input_usd_per_million: float | None = Field(default=None, ge=0)
    structure_review_output_usd_per_million: float | None = Field(default=None, ge=0)
    structure_review_max_cost_usd: float = Field(default=0.3, gt=0, le=5)
    # 문서 메타데이터(기관·문서 이름·종류·기준 시점)를 뽑는다. 구조 검토와 같은 모델·단가를 쓰고 문서당 1회 호출한다.
    # 모델·단가가 없거나 allow_external이 아니면 건너뛴다. 실패해도 검색 준비는 계속한다.
    metadata: bool = False


class QuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    question: str = Field(min_length=1, max_length=2000, pattern=r"\S")
    mode: str = Field(default="hybrid", pattern=r"^(lexical|dense|hybrid)$")
    answer: bool = False
    allow_external: bool = False
    limit: int = Field(default=8, ge=1, le=20)
    embedding: EmbeddingConfig | None = None
    model: str = Field(default="gpt-4.1", min_length=1, max_length=100)
    input_usd_per_million: float | None = Field(default=None, ge=0)
    output_usd_per_million: float | None = Field(default=None, ge=0)
    max_cost_usd: float = Field(default=0.15, gt=0, le=10)
    # 질문 이해 에이전트(ADR-079): 계획 모델 호출 1회와 구조 일치·1회 재검색을 추가한다. 답변 생성이 필요하다.
    agent: bool = False
    # selection: 근거 값 선택(작은 모델에서도 안정적), narrative: 문장별 인용(검사가 더 엄격)
    answer_mode: str = Field(default="selection", pattern=r"^(selection|narrative)$")
    # 질문 이해 계획 모델을 답변 모델과 따로 지정할 수 있다. 비우면 답변 모델·단가를 쓴다.
    plan_model: str | None = Field(default=None, min_length=1, max_length=100)
    plan_input_usd_per_million: float | None = Field(default=None, ge=0)
    plan_output_usd_per_million: float | None = Field(default=None, ge=0)
    plan_max_cost_usd: float = Field(default=0.05, gt=0, le=10)
    # 리랭커(선택). 모델과 단가를 명시해야 켜진다. 근거 상위 30개를 다시 정렬해 max_evidence개만 답변에 넘긴다.
    rerank_model: str | None = Field(default=None, min_length=1, max_length=100)
    rerank_input_usd_per_million: float | None = Field(default=None, ge=0)
    rerank_output_usd_per_million: float | None = Field(default=None, ge=0)
    rerank_max_cost_usd: float = Field(default=0.05, gt=0, le=10)
    max_evidence: int = Field(default=12, ge=1, le=40)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    # 기록 중인 상태 파일을 읽지 않도록 같은 폴더에서 원자적으로 교체한다.
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def embedding_audit(clients):
    """작업에서 만든 임베딩 클라이언트들의 호출·예약·추정 비용을 합친다."""
    calls = [c for client in clients for c in client.audit]
    return dict(calls=calls, reserved_cost_usd=sum(client.reserved_cost_usd for client in clients),
                estimated_cost_usd=sum(c.get("estimated_cost_usd", 0) for c in calls),
                cost_complete=all("estimated_cost_usd" in c for c in calls))


def scoped(retriever, run_ids):
    """검색을 주어진 범위로 제한한다: 실행 목록, 또는 코퍼스 전체(CorpusScope — 실행 ID를 나열하지 않는다).
    키워드(None)·벡터·하이브리드 검색기 모두 run_ids를 받는다."""
    from doc_understanding.rag.retrieval.scope import CorpusScope

    def scope():
        return run_ids if isinstance(run_ids, CorpusScope) else list(run_ids)

    def search(question, _run_id=None, limit=8):
        # 키워드 검색 함수는 호출 시점에 찾는다(테스트·재시작 없이 교체된 구현도 따른다).
        return (retriever or repository.search)(question, None, limit, run_ids=scope())

    def many(questions, limit=8):
        # 여러 검색어를 한 번에: 벡터 검색기는 search_many, 키워드만이면 키워드 일괄 SQL.
        if retriever is not None and hasattr(retriever, "search_many"):
            return retriever.search_many(questions, None, limit, run_ids=scope())
        if retriever is None:
            return repository.search_many(questions, limit, run_ids=scope())
        return [search(q, None, limit) for q in questions]

    search.many = many
    client = getattr(retriever, "client", None)
    if client is not None:
        # 구조 일치의 의미 유사도에 같은 임베딩 클라이언트(같은 프로필·예산·기록)를 쓴다.
        search.embed = client.embed
        search.profile_id = client.config.profile_id
    return search


class WebRagError(ValueError):
    pass


def safe_error(error):
    # SQL·제공자 응답·문서 내용·키가 포함될 수 있는 임의 예외 메시지는 반환하지 않는다.
    if isinstance(error, WebRagError):
        return str(error)
    code = str(error)
    allowed = {
        "API_KEY_UNAVAILABLE",
        "PGVECTOR_EXTENSION_NOT_AVAILABLE",
        "EMBEDDING_RUN_BUDGET_EXCEEDED",
        "MODEL_REQUEST_BUDGET_EXCEEDED",
        "EMBEDDING_CALL_FAILED_SEE_AUDIT",
        "ANSWER_CALL_FAILED_SEE_LOCAL_AUDIT",
        "NO_MATCHING_EMBEDDINGS",
        "SOURCE_ARTIFACT_CHANGED",
        "DOCUMENT_IR_NOT_IDENTICAL",
    }
    if code in allowed:
        return code
    state = getattr(error, "sqlstate", None)
    if state and re.fullmatch(r"[0-9A-Z]{5}", state):
        return "LOCAL_POSTGRES_STATEMENT_FAILED SQLSTATE=" + state
    if code.startswith("LOCAL_POSTGRES_STATEMENT_FAILED"):
        return "LOCAL_POSTGRES_STATEMENT_FAILED"
    return "RAG_OPERATION_FAILED"


# 작업 줄: 검색 준비·적재(쓰기)와 질문(읽기)은 서로를 막지 않는다. 같은 줄에서는 한 번에 하나만 실행한다.
# 색인은 문서마다 한 트랜잭션으로 바뀌므로 질문은 적재 전이나 후의 온전한 색인만 본다. 새 문서는 색인 직후
# 키워드로 검색되고 임베딩이 끝나면 벡터로도 검색된다(그 사이의 상태는 문서 목록에 '임베딩 중'으로 보인다).
LANES = dict(prepare="ingest", ingest="ingest", question="question")


class WebRagService:
    def __init__(self, documents, output):
        self.documents, self.output = Path(documents), Path(output)
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=len(set(LANES.values())), thread_name_prefix="web-rag")
        self.lanes = {}
        # 단일 서버 프로세스에서만 작업한다. 재시작은 유료 작업을 자동 재시도하지 않는다.
        for path in self.output.glob("*/tasks/*.json"):
            task = read_json(path)
            if task["status"] in ("queued", "running"):
                task.update(status="interrupted", error="SERVER_RESTARTED")
                self._save(task)

    def close(self):
        self.pool.shutdown(wait=True)

    @property
    def active(self):
        """실행 중인 작업의 요청 ID(아무 줄이나). 없으면 None."""
        return next(iter(self.lanes.values()), None)

    @active.setter
    def active(self, value):
        # 예전 코드·테스트가 쓰던 단일 칸 표기: None은 모든 줄을 비운다.
        self.lanes = {} if value is None else dict(ingest=value)

    def claim(self, action, request_id):
        """그 작업의 줄을 차지한다(잠금을 쥔 채 호출). 같은 줄이 실행 중이면 RAG_BUSY."""
        lane = LANES[action]
        if self.lanes.get(lane):
            raise WebRagError("RAG_BUSY")
        self.lanes[lane] = request_id

    def release(self, action):
        with self.lock:
            self.lanes.pop(LANES[action], None)

    def busy(self):
        return dict(busy=bool(self.lanes), busy_ingest="ingest" in self.lanes, busy_question="question" in self.lanes)

    def _folder(self, jid):
        if not re.fullmatch(r"[a-f0-9]{32}", jid):
            raise WebRagError("DOCUMENT_NOT_FOUND")
        return self.documents / jid

    def document(self, jid):
        path = self._folder(jid) / "document.json"
        if not path.is_file():
            raise WebRagError("DOCUMENT_NOT_READY")
        raw = path.read_bytes()
        return CanonicalDocument.model_validate_json(raw), hashlib.sha256(raw).hexdigest()

    def document_digest(self, jid):
        """문서 해시. 상태 조회는 2.5초마다 오므로 파일이 그대로면(수정 시각·크기) 문서를 다시 풀어 검증하지 않는다."""
        path = self._folder(jid) / "document.json"
        try:
            stat = path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            stamp = None
        cache = self.__dict__.setdefault("_digests", {})
        if stamp is None or cache.get(jid, (None, None))[0] != stamp:
            cache[jid] = (stamp, self.document(jid)[1])
        return cache[jid][1]

    def manifest(self, jid, doc):
        folder = self._folder(jid)
        candidates = list((folder / "extraction").glob("*/staging-manifest.json"))
        review_path = folder / "review.json"
        if review_path.exists():
            run = read_json(review_path).get("extraction_run")
            if run:
                candidates.append(Path(run) / "staging-manifest.json")
        matches = []
        for path in dict.fromkeys(candidates):
            if path.is_file():
                source = Path(read_json(path)["run_dir"]) / "document-ir.json"
                if source.is_file() and read_json(source) == doc.document_ir.model_dump(
                    mode="json"
                ):
                    matches.append(path)
        if len(matches) != 1:
            raise WebRagError("SOURCE_MANIFEST_REQUIRED")
        return matches[0]

    def _save(self, task):
        with self.lock:
            task["updated_at"] = datetime.now(timezone.utc).isoformat()
            folder = self.output / task["job_id"]
            write_json(folder / "tasks" / (task["request_id"] + ".json"), task)
            write_json(folder / ("latest-" + task["action"] + ".json"), task)

    def state(self, jid):
        self._folder(jid)
        with self.lock:
            result = {"api_key_available": bool(os.environ.get("OPENAI_API_KEY"))}
            for action in ("prepare", "question"):
                path = self.output / jid / ("latest-" + action + ".json")
                result[action] = read_json(path) if path.exists() else None
            result.update(self.busy())
            result["index_version"] = INDEX_VERSION
            # 질문 가능 여부: 현재 문서·색인 버전으로 검색 준비(DB 적재)가 끝났는지. 화면 안내에 쓴다.
            try:
                self._prepared(jid, self.document_digest(jid))
                result["prepared_current"] = True
            except (WebRagError, ValueError, OSError):
                result["prepared_current"] = False
            return result

    def start(self, jid, action, request):
        doc, digest = self.document(jid)
        data = request.model_dump(mode="json")
        signature = input_hash(json.dumps([action, digest, data], sort_keys=True))
        path = self.output / jid / "tasks" / (request.request_id + ".json")
        with self.lock:
            if path.exists():
                task = read_json(path)
                if task["signature"] != signature:
                    raise WebRagError("REQUEST_ID_CONFLICT")
                return task
            if self.lanes.get(LANES[action]):
                raise WebRagError("RAG_BUSY")
            self._check(action, request)
            if action == "prepare":
                self.manifest(jid, doc)
            else:
                self._prepared(jid, digest)
            task = dict(
                job_id=jid,
                request_id=request.request_id,
                action=action,
                signature=signature,
                document_sha256=digest,
                index_version=INDEX_VERSION,
                config=data,
                status="queued",
                stage="queued",
            )
            self._save(task)
            self.claim(action, request.request_id)
            try:
                self.pool.submit(self._execute, task, request)
            except Exception:
                self.release(action)
                task.update(status="failed", error="WORKER_START_FAILED")
                self._save(task)
                raise WebRagError("WORKER_START_FAILED") from None
            return dict(task)

    def _check(self, action, request):
        """외부 호출 동의·키·단가 등 실행 전 검사. 문서별 작업과 코퍼스 작업이 같이 쓴다."""
        if action == "question" and request.agent and not request.answer:
            raise WebRagError("AGENT_REQUIRES_ANSWER")
        embeds = request.embed if action == "prepare" else request.mode != "lexical"
        if embeds and not request.embedding:
            raise WebRagError("EMBEDDING_CONFIG_REQUIRED")
        # 로컬 임베딩은 이 컴퓨터 안에서 처리한다. 외부 동의·키는 OpenAI 임베딩과 답변 생성에만 요구한다.
        external = (embeds and request.embedding.external) or (
            action == "question" and request.answer
        )
        if external and not request.allow_external:
            raise WebRagError("EXTERNAL_CALL_NOT_ENABLED")
        if external and not os.environ.get("OPENAI_API_KEY"):
            raise WebRagError("API_KEY_UNAVAILABLE")
        if (
            action == "question"
            and request.answer
            and (
                request.input_usd_per_million is None or request.output_usd_per_million is None
            )
        ):
            raise WebRagError("EXPLICIT_MODEL_PRICING_REQUIRED")

    def _run_agent(self, task, request, run_ids, retriever, max_documents=1):
        """질문 이해 에이전트(ADR-079). 계획 모델과 답변 모델은 같은 모델·단가를 쓰고 한도는 호출별이다."""
        from doc_understanding.rag.agentic.plan import PlanCache
        from doc_understanding.rag.agentic.run import answer as agent_answer
        from doc_understanding.runtime.structured_call import CallPolicy

        task["stage"] = "planning"
        self._save(task)
        prices = dict(input_usd_per_million=request.input_usd_per_million,
                      output_usd_per_million=request.output_usd_per_million)
        if request.plan_model:
            if request.plan_input_usd_per_million is None or request.plan_output_usd_per_million is None:
                raise WebRagError("EXPLICIT_MODEL_PRICING_REQUIRED")
            policy = CallPolicy(model=request.plan_model, max_cost_usd=request.plan_max_cost_usd,
                                input_usd_per_million=request.plan_input_usd_per_million,
                                output_usd_per_million=request.plan_output_usd_per_million)
        else:
            policy = CallPolicy(model=request.model, max_cost_usd=request.max_cost_usd, **prices)
        reranker = None
        if request.rerank_model:
            from doc_understanding.rag.agentic.rerank import LLMListwiseReranker
            from doc_understanding.runtime.structured_call import Ledger
            if request.rerank_input_usd_per_million is None or request.rerank_output_usd_per_million is None:
                raise WebRagError("EXPLICIT_MODEL_PRICING_REQUIRED")
            rerank_policy = CallPolicy(model=request.rerank_model, max_cost_usd=request.rerank_max_cost_usd,
                                       input_usd_per_million=request.rerank_input_usd_per_million,
                                       output_usd_per_million=request.rerank_output_usd_per_million)
            reranker = LLMListwiseReranker(rerank_policy, allow_external=True,
                                           ledger=Ledger(max_calls=2, max_total_usd=2 * request.rerank_max_cost_usd))
        output = self.output / task["job_id"] / "answers" / request.request_id
        trace = self._trace()

        def progress(stage):
            # 화면에 지금 실행 중인 단계를 보여 준다(에이전트가 실제로 들어간 단계만 알린다).
            task["stage"] = stage
            self._save(task)

        hooks = {}
        from doc_understanding.rag.retrieval.scope import CorpusScope
        if isinstance(run_ids, CorpusScope) or len(run_ids) > 1:
            from doc_understanding.rag.agentic import directory as agent_directory
            selected = None if isinstance(run_ids, CorpusScope) else run_ids
            hooks = dict(directory=lambda: agent_directory.documents(INDEX_VERSION, selected),
                         narrow=lambda ids: scoped(retriever, ids), mentions=agent_directory.mentions(INDEX_VERSION, selected),
                         explicit_scope=selected is not None)
        # 구조가 확정되지 않은 표의 원문 격자 읽기: 질문 이해 모델(작은 모델)과 같은 모델·단가를 쓴다.
        from doc_understanding.services import source_view
        hooks.update(grid_loader=source_view.table_grid, grid_shapes=source_view.table_shapes,
                     grid_policy=policy.model_copy(update=dict(max_output_tokens=600)))
        try:
            report = agent_answer(request.question, output, trace=trace, run_id=None, progress=progress, **hooks,
                                  retriever=scoped(retriever, run_ids), max_documents=max_documents,
                                  plan_policy=policy, plan_cache=PlanCache(self.output / "plan-cache"),
                                  allow_external=True, answer_mode=request.answer_mode, presentation=True,
                                  reranker=reranker, max_evidence=request.max_evidence,
                                  model=request.model, max_cost_usd=request.max_cost_usd, **prices)
        finally:
            if retriever:
                task["retrieval_audit"] = retriever.report()
        # 되묻기·문서 없음으로 끝난 질문은 검색을 하지 않아 시도 기록이 없다.
        last = report["attempts"][-1]["trace"] if report["attempts"] else dict(hits=[], tables=[], structured=[])
        task.update(hits=last["hits"], evidence=report["evidence"], result=report["result"],
                    agent=dict(plan=report["plan"], plan_check={k: v for k, v in report["plan_check"].items()
                                                                if k != "call"},
                               retried=report["retried"], tables=last["tables"], structured=last["structured"],
                               documents=report.get("documents"),
                               plan_model_calls=report["plan_model_calls"],
                               answer_model_calls=report["answer_model_calls"],
                               plan_estimated_cost_usd=report["plan_estimated_cost_usd"],
                               rerank={k: v for k, v in (last.get("rerank") or {}).items() if k != "scores"} or None,
                               rerank_estimated_cost_usd=report.get("rerank_estimated_cost_usd"),
                               grid=last.get("grid"), grid_estimated_cost_usd=report.get("grid_estimated_cost_usd"),
                               trace_id=report.get("trace_id"),
                               trace_warnings=report.get("trace_warnings", [])))

    def _trace(self):
        """로컬 Langfuse 설정이 있으면 추적을 켠다. 설정은 루프백 주소만 허용되며 실패해도 실행은 계속된다."""
        from doc_understanding.runtime.paths import ROOT
        from doc_understanding.runtime.telemetry import Trace, load_config

        path = ROOT / "infra/langfuse/client.env.local"
        if not path.is_file():
            return None
        try:
            return Trace(load_config(path))
        except Exception:
            return None

    def _review(self, task, doc, source, request=None):
        """구조 검토. 규칙으로 판정하고, 요청이 모델을 지정했으면 규칙이 모호로 남긴 표에만 모델을 쓴다.

        개선이 있으면 새 폴더에 검토 후보 문서를 쓰고 그 경로를 반환한다.
        검토가 실패해도 검색 준비는 원본 문서로 계속한다. 원본 문서는 수정하지 않는다.
        """
        from doc_understanding.agents.structure_review.review import review_document
        from doc_understanding.runtime.structured_call import CallPolicy, Ledger

        folder = self.output / task["job_id"] / "reviewed" / task["request_id"]
        folder.mkdir(parents=True, exist_ok=False)
        options = {}
        if (request is not None and request.structure_review_model and request.allow_external
                and request.structure_review_input_usd_per_million is not None
                and request.structure_review_output_usd_per_million is not None):
            options = dict(
                policy=CallPolicy(model=request.structure_review_model,
                                  input_usd_per_million=request.structure_review_input_usd_per_million,
                                  output_usd_per_million=request.structure_review_output_usd_per_million,
                                  max_cost_usd=0.03),
                ledger=Ledger(max_calls=40, max_total_usd=request.structure_review_max_cost_usd),
                allow_external=True, output=folder / "model-calls")
        try:
            reviewed, report = review_document(doc, **options)
        except Exception as error:
            return source, dict(status="failed", error=type(error).__name__)
        write_json(folder / "structure-review.json", report)
        summary = dict(report["summary"], applied=len(report["applied"]), rejected=len(report["rejected"]),
                       model_calls=len(report.get("model_calls") or []),
                       estimated_model_cost_usd=report.get("estimated_model_cost_usd", 0.0),
                       report=str(folder / "structure-review.json"))
        if reviewed is None:
            return source, dict(summary, status="no_change")
        path = folder / "document.json"
        with path.open("x", encoding="utf-8") as stream:
            stream.write(reviewed.model_dump_json(indent=2))
        return path, dict(summary, status="applied", document=str(path))

    def _metadata(self, task, doc, request):
        """문서 메타데이터를 뽑아 저장한다. 같은 지시문으로 이미 뽑은 문서는 다시 부르지 않는다. 실패는 기록만 한다."""
        from doc_understanding.rag.metadata import extract as extraction
        from doc_understanding.rag.metadata import store
        from doc_understanding.runtime.structured_call import CallPolicy

        if not (request.structure_review_model and request.allow_external
                and request.structure_review_input_usd_per_million is not None
                and request.structure_review_output_usd_per_million is not None):
            return dict(status="skipped", reason="MODEL_NOT_CONFIGURED")
        try:
            if doc.document_id in store.extracted_with([doc.document_id], extraction.PROMPT_SHA256):
                return dict(status="kept")
            policy = CallPolicy(model=request.structure_review_model, max_cost_usd=0.03,
                                input_usd_per_million=request.structure_review_input_usd_per_million,
                                output_usd_per_million=request.structure_review_output_usd_per_million)
            folder = self.output / task["job_id"] / "metadata" / task["request_id"]
            folder.parent.mkdir(parents=True, exist_ok=True)
            result, record = extraction.extract(doc, policy, folder, allow_external=True)
            store.put_extracted(doc.document_id, result)
            return dict(status=result["status"], fields=sorted(result["fields"]), dropped=result["dropped"],
                        estimated_model_cost_usd=(record or {}).get("estimated_cost_usd", 0.0))
        except Exception as error:
            return dict(status="failed", error=type(error).__name__)

    def _index_document(self, jid, doc, digest):
        # 검색 준비에서 검토 후보 문서로 색인했다면 미리보기도 같은 문서로 만든다.
        path = self.output / jid / "stored.json"
        if path.exists():
            stored = read_json(path)
            reviewed = stored.get("reviewed_document")
            if reviewed and stored["document_sha256"] == digest and Path(reviewed).is_file():
                return CanonicalDocument.model_validate_json(Path(reviewed).read_bytes())
        return doc

    def _prepared(self, jid, digest):
        path = self.output / jid / "stored.json"
        if not path.exists():
            raise WebRagError("SEARCH_PREPARATION_REQUIRED")
        stored = read_json(path)
        if stored["document_sha256"] != digest or stored["index_version"] != INDEX_VERSION:
            raise WebRagError("SEARCH_PREPARATION_STALE")
        return stored

    def _prepare_job(self, item, jid, doc, digest, request, save, clients):
        """구조 검토 → DB 적재·색인 → (선택) 문서 메타데이터 → (선택) 임베딩. item에 진행 상태를 기록하고 임베딩 클라이언트는 clients에 모은다."""
        original = source = self._folder(jid) / "document.json"
        if request.structure_review:
            item["stage"] = "reviewing"
            save()
            source, item["structure_review"] = self._review(item, doc, original, request)
            save()
        stored = repository.store(self.manifest(jid, doc), source)
        # 작업 식별은 원본 document.json 해시로 한다. 검토 후보로 색인했으면 그 문서 해시는 따로 남긴다.
        # (색인한 문서 해시를 그대로 두면 질문 시 원본과 달라 SEARCH_PREPARATION_STALE로 거절됐다.)
        stored.update(index_version=INDEX_VERSION,
                      indexed_document_sha256=stored.get("document_sha256"),
                      document_sha256=digest,
                      reviewed_document=str(source) if source != original else None)
        write_json(self.output / jid / "stored.json", stored)
        item.update(run_id=stored["run_id"], total=stored["search_entries"])
        if request.metadata:
            item["stage"] = "describing"
            save()
            item["metadata"] = self._metadata(item, doc, request)
            save()
        if request.embed:
            item["stage"] = "embedding"
            save()
            vectors.migrate()
            client = EmbeddingClient(request.embedding, allow_external=True)
            clients.append(client)

            def progress(counts):
                item.update(counts)
                save()

            result = embed_run(stored["run_id"], client, progress=progress)
            item.update(result)
            item["status"] = (
                "ready" if result["coverage_complete"] and result["total"] else "partial"
            )
        else:
            item["status"] = "stored"

    def _question(self, task, request, run_ids, clients, *, max_documents=1):
        """run_ids 범위에서 검색·근거 복원·(선택) 답변 또는 질문 이해 에이전트를 실행한다."""
        retriever = None
        if request.mode != "lexical":
            client = EmbeddingClient(request.embedding, allow_external=True)
            clients.append(client)
            retriever = Retriever(request.mode, client)
        if request.agent:
            self._run_agent(task, request, run_ids, retriever, max_documents=max_documents)
            return
        task["stage"] = "searching"
        self._save(task)
        try:
            hits = scoped(retriever, run_ids)(request.question, None, request.limit)
        finally:
            if retriever:
                task["retrieval_audit"] = retriever.report()
        task["stage"] = "evidence"
        self._save(task)
        evidence = resolve_evidence(hits, request.question, max_documents=max_documents)
        task.update(hits=hits, evidence=evidence)
        if request.answer:
            task["stage"] = "answering"
            self._save(task)
            output = self.output / task["job_id"] / "answers" / request.request_id
            task["result"] = generate_answer(
                request.question,
                evidence,
                output,
                allow_external=True,
                answer_mode=request.answer_mode, presentation=True,
                verify_sql=True,
                model=request.model,
                input_usd_per_million=request.input_usd_per_million,
                output_usd_per_million=request.output_usd_per_million,
                max_cost_usd=request.max_cost_usd,
            )

    def _execute(self, task, request):
        clients = []
        try:
            task.update(
                status="running", stage="storing" if task["action"] == "prepare" else
                "planning" if request.agent else "searching"
            )
            self._save(task)
            doc, digest = self.document(task["job_id"])
            if digest != task["document_sha256"]:
                raise WebRagError("DOCUMENT_CHANGED")
            if task["action"] == "prepare":
                self._prepare_job(task, task["job_id"], doc, digest, request, lambda: self._save(task), clients)
            else:
                stored = self._prepared(task["job_id"], digest)
                task["run_id"] = stored["run_id"]
                self._question(task, request, [stored["run_id"]], clients, max_documents=1)
                task["status"] = "completed"
            task["stage"] = "finished"
        except Exception as error:
            task.update(status="failed", error=safe_error(error))
            # 답변 호출 후 검사가 실패해도 이미 예약한 비용 기록은 확인할 수 있게 남긴다.
            if task["action"] == "question":
                base = self.output / task["job_id"] / "answers" / request.request_id
                # 일반 답변은 폴더 바로 아래, 에이전트 답변은 answer*/ 아래에 실패 기록이 남는다(answer, answer-retry,
                # 표 전체로 넓힌 answer-whole·answer-retry-whole, 넓히기 전 근거로 되돌린 *-focused). 가장 나중 것을 쓴다.
                failures = [base / "failure.json", *base.glob("answer*/failure.json")] if base.is_dir() else []
                failures = sorted((f for f in failures if f.exists()), key=lambda f: f.stat().st_mtime_ns)
                if failures:
                    task["answer_failure_audit"] = read_json(failures[-1])
        finally:
            if clients:
                task["embedding_audit"] = embedding_audit(clients)
            try:
                self._save(task)
            finally:
                self.release(task["action"])

    def entries(self, jid, *, offset=0, limit=20, kind=None, page=None):
        doc, digest = self.document(jid)
        doc = self._index_document(jid, doc, digest)
        entries = make_entries(doc)
        state = self.state(jid)["prepare"]
        config = state.get("config", {}).get("embedding") if state else None
        cfg = EmbeddingConfig.model_validate(config) if config else None
        cached, db_error = {}, None
        # 저장한 프로필과 현재 원문을 대조하여 이전 성공 표시를 잘못 재사용하지 않는다.
        if (
            cfg
            and state.get("run_id")
            and state["document_sha256"] == digest
            and state["index_version"] == INDEX_VERSION
        ):
            try:
                cached = vectors.cached_hashes(state["run_id"], cfg)
            except Exception as error:
                db_error = safe_error(error)
        kinds = {}
        counts = {"embedded": 0, "pending": 0, "excluded": 0, "unknown": 0}
        for entry in entries:
            kinds[entry["kind"]] = kinds.get(entry["kind"], 0) + 1
            text = entry["text"]
            status, reason = "pending", None
            if cfg and (not text.strip() or len(text.encode("utf-8")) > cfg.max_input_bytes):
                status, reason = "excluded", "EMPTY_OR_OVERSIZED_INPUT"
            elif db_error:
                status, reason = "unknown", db_error
            elif cached.get(entry["entry_id"]) == input_hash(text):
                status = "embedded"
            entry.update(
                embedding_status=status,
                exclusion_reason=reason,
                input_bytes=len(text.encode("utf-8")),
            )
            counts[status] += 1
        filtered = [
            e
            for e in entries
            if (not kind or e["kind"] == kind) and (not page or e["page"] == page)
        ]
        visible = filtered[offset : offset + limit]
        # 임베딩 텍스트는 바꾸지 않고 화면용 원문 셀·행열 경로만 별도로 덧붙인다.
        from doc_understanding.rag.evidence.cells import cells_for_table

        table_cells = {}
        for entry in visible:
            tid = entry.get("table_id")
            if tid and entry.get("cell_ids"):
                if tid not in table_cells:
                    table_cells[tid] = {c["cell_id"]: c for c in cells_for_table(doc, tid)}
                entry["source_cells"] = [
                    {
                        k: table_cells[tid][cid].get(k)
                        for k in (
                            "cell_id",
                            "value_raw",
                            "row_path",
                            "column_path",
                            "lookup_eligible",
                            "source",
                        )
                    }
                    for cid in entry["cell_ids"]
                    if cid in table_cells[tid]
                ]
        return dict(
            total=len(entries),
            filtered=len(filtered),
            kinds=kinds,
            counts=counts,
            profile=cfg.profile if cfg else None,
            database_error=db_error,
            items=visible,
            offset=offset,
            limit=limit,
            text_basis="current_search_projection",
            index_version=INDEX_VERSION,
        )
