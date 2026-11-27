# 역할: 질문을 인용 구간에 고정된 요구 사항과 검색어로 바꾸는 계획 계약과 검사. 질문 의미를 규칙으로 판정하지 않는다.
import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from doc_understanding.rag.prompts.planner import SYSTEM
from doc_understanding.runtime.structured_call import structured_call


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_quote: str = Field(min_length=1, max_length=400)
    role: Literal["scope", "item", "condition", "other"]
    text: str = Field(min_length=1, max_length=200)


class Ambiguity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_quote: str = Field(min_length=1, max_length=400)
    readings: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(min_length=2, max_length=3)
    follow_up: str = Field(min_length=2, max_length=400)


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_quote: str = Field(min_length=1, max_length=400)
    requirement: str = Field(min_length=1, max_length=300)
    kind: Literal['name', 'procedure', 'amount', 'date', 'condition', 'reason', 'other']


def _all_required(schema):
    # 모델 응답 계약(strict)은 모든 항목을 필수로 요구한다. 코드에서는 reading을 생략할 수 있게 기본값을 둔다.
    schema["required"] = list(schema["properties"])
    for name in ("reading", "subjects", "ambiguity", "answer_request"):
        schema["properties"][name].pop("default", None)


class QuestionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra=_all_required)
    ambiguity: Ambiguity | None = None
    answer_request: AnswerRequest | None = None
    requirements: list[Requirement] = Field(min_length=1, max_length=12)
    queries: list[str] = Field(min_length=1, max_length=4)
    # 읽기 범위: focused = 가장 가까운 근거 몇 개로 답한다. whole = 근거가 속한 표를 통째로 읽고 답한다(graph.reading_scope).
    reading: Literal["focused", "whole"] = "focused"
    # 질문이 가리킨 대상(기관·상품·문서 이름)을 질문 문구 그대로. 문서 고르기(agentic.documents)가 문서 이름과 맞춘다.
    subjects: list[Annotated[str, Field(max_length=200)]] = Field(default_factory=list, max_length=3)


def norm(text):
    return re.sub(r"\s+", "", text or "")


def validate_plan(plan, question):
    """인용 구간이 질문에 실제로 있는지, 검색어가 비어 있지 않은지 검사한다. 의미 정확도는 판정하지 않는다."""
    reasons = []
    for r in plan.requirements:
        if norm(r.question_quote) not in norm(question):
            reasons.append("REQUIREMENT_NOT_ANCHORED_IN_QUESTION")
    if any(not q.strip() or len(q) > 200 for q in plan.queries):
        reasons.append("INVALID_SEARCH_QUERY")
    if plan.answer_request and plan.answer_request.question_quote not in question:
        reasons.append("ANSWER_REQUEST_NOT_ANCHORED_IN_QUESTION")
    if plan.ambiguity:
        a = plan.ambiguity
        if (not a.question_quote.strip() or a.question_quote not in question
                or len({norm(s) for s in a.readings if s.strip()}) < 2 or not a.follow_up.strip()):
            reasons.append("INVALID_AMBIGUITY")
    return dict(passed=not reasons, reasons=sorted(set(reasons)), semantic_status="model_plan_unvalidated")


def fallback_plan(question):
    """계획 호출이 실패하거나 검사를 통과하지 못하면 질문 원문 하나로 검색한다."""
    return QuestionPlan(requirements=[Requirement(question_quote=question[:400], role="other",
                                                  text=question[:200])], queries=[question[:200]])


class PlanCache:
    """같은 질문은 같은 계획으로 검색하도록, 검사를 통과한 계획을 파일로 보관한다.

    온도 0이어도 계획 모델은 실행마다 검색어를 조금씩 다르게 낸다. 검색어가 달라지면 찾는 근거와 답이 달라지므로
    질문 문구·모델·지시문이 모두 같을 때는 처음 통과한 계획을 다시 쓴다. 지시문이나 모델을 바꾸면 키가 달라져 새로 만든다."""

    def __init__(self, folder):
        self.folder = Path(folder)

    def path(self, question, model):
        key = json.dumps([model, SYSTEM, question], ensure_ascii=False).encode("utf8")
        return self.folder / (hashlib.sha256(key).hexdigest() + ".json")

    def get(self, question, model):
        try:
            return QuestionPlan.model_validate(json.loads(self.path(question, model).read_text(encoding="utf8"))["plan"])
        except (OSError, ValueError, KeyError, TypeError):
            return None  # 없거나 읽을 수 없으면 새로 만든다.

    def put(self, question, model, plan):
        target = self.path(question, model)
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            part = target.with_name(target.name + "." + uuid.uuid4().hex + ".part")
            part.write_text(json.dumps(dict(question=question, model=model, plan=plan.model_dump()),
                                       ensure_ascii=False, indent=1), encoding="utf8")
            os.replace(part, target)
        except OSError:
            pass  # 보관 실패는 질문 실행을 막지 않는다.


def make_plan(question, policy, output, *, allow_external, ledger=None, transport=None, cache=None):
    """계획 모델을 한 번 호출한다. 실패·검사 실패면 원문 계획으로 대체하고 이유를 남긴다.
    cache(PlanCache)에 같은 질문의 통과한 계획이 있으면 모델을 부르지 않고 그것을 쓴다."""
    if cache is not None:
        kept = cache.get(question, policy.model)
        if kept is not None:
            check = validate_plan(kept, question)
            if check["passed"]:
                return kept, dict(check, fallback=False, cached=True)
    kwargs = dict(allow_external=allow_external, ledger=ledger)
    if transport:
        kwargs["transport"] = transport
    try:
        plan, record = structured_call("question_plan", SYSTEM, dict(question=question), QuestionPlan,
                                       policy, output, **kwargs)
    except (RuntimeError, ValueError) as error:
        return fallback_plan(question), dict(passed=False, reasons=["PLAN_CALL_FAILED:" + str(error)],
                                              fallback=True)
    check = validate_plan(plan, question)
    if not check["passed"]:
        return fallback_plan(question), dict(check, fallback=True, rejected_plan=plan.model_dump())
    if cache is not None:
        cache.put(question, policy.model, plan)
    return plan, dict(check, fallback=False, call=record)
