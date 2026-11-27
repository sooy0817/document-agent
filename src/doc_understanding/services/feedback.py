# 역할: 검색·답변 화면에서 사용자가 남긴 문제 표시(답변 오류·근거 누락·잘못된 인용)를 질문·답변·인용 근거와 함께 기록한다.
"""사용자 의견 기록.

- 기록은 실패 장부(evaluation.failure_log)에 덧붙인다: 의견마다 work/feedback/<의견 ID>/failures.json 하나와
  work/failures/ledger.jsonl 한 줄(kind="feedback", label=질문 요청 ID). 채점 실패 기록과 같은 형식이라 함께 볼 수 있다.
- 의견은 검토 전 원래 의견이다. 정답 데이터(config/evaluation)·파서·색인을 바꾸지 않으며, 무엇도 이 기록을 자동으로
  읽어 반영하지 않는다. 검토 결과는 review 칸에 따로 적는다(지금은 항상 미검토). 정답으로 삼으려면 사람이 검토해
  평가셋에 직접 옮겨야 한다.
- 질문·답변·인용 근거는 서버에 저장된 그 질문의 실행 기록에서 읽는다(화면이 보낸 내용을 믿지 않는다).
"""
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from doc_understanding.evaluation import failure_log

KINDS = dict(answer_error="답변 오류", missing_evidence="근거 누락", wrong_citation="잘못된 인용")
SET_NAME = "user-screen"


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    kinds: list[Literal["answer_error", "missing_evidence", "wrong_citation"]] = Field(min_length=1, max_length=3)
    comment: str | None = Field(default=None, max_length=2000)
    # 문제가 있다고 표시한 인용 근거(그 답변의 근거 ID).
    evidence_ids: list[str] = Field(default_factory=list, max_length=40)


def _evidence(e):
    return dict(evidence_id=e.get("evidence_id"), run_id=e.get("run_id"), document_id=e.get("document_id"),
                source_name=e.get("source_name"), page=e.get("page"), table_id=e.get("table_id"),
                cell_id=e.get("cell_id"), content_id=e.get("content_id"), chunk_id=e.get("chunk_id"),
                value_raw=e.get("value_raw"), row_path=e.get("row_path") or [], column_path=e.get("column_path") or [],
                lookup_eligible=e.get("lookup_eligible"))


def build(task, payload):
    """질문 실행 기록과 사용자 표시로 의견 한 건을 만든다."""
    result = task.get("result") or {}
    selection = result.get("selection") or {}
    evidence = task.get("evidence") or []
    known = {e.get("evidence_id") for e in evidence}
    unknown = [i for i in payload.evidence_ids if i not in known]
    if unknown:
        raise ValueError("EVIDENCE_NOT_IN_ANSWER")
    cited = set(selection.get("evidence_ids") or [])
    kinds = list(dict.fromkeys(payload.kinds))
    return dict(
        feedback_id=uuid.uuid4().hex, at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        source="search_screen", kinds=kinds, labels=[KINDS[k] for k in kinds],
        # 원래 의견(검토 전). 검토 결과와 섞지 않는다.
        opinion=dict(comment=(payload.comment or "").strip() or None, evidence_ids=list(payload.evidence_ids)),
        review=dict(status="unreviewed", reviewed_by=None, reviewed_at=None, conclusion=None),
        question=(task.get("config") or {}).get("question"),
        answer=dict(text=result.get("answer_text") or result.get("answer"), source=result.get("answer_text_source"),
                    abstained=bool(selection.get("abstained", result.get("abstained", False))),
                    values_raw=list(selection.get("values_raw") or []), reasons=result.get("reasons")),
        run=dict(request_id=task.get("request_id"), status=task.get("status"), error=task.get("error"),
                 index_version=task.get("index_version"), scope=task.get("scope"),
                 trace_id=(task.get("agent") or {}).get("trace_id"),
                 answer_folder=f"work/web-rag/{task.get('job_id')}/answers/{task.get('request_id')}"),
        cited_evidence=[_evidence(e) for e in evidence if e.get("evidence_id") in cited],
        flagged_evidence=[_evidence(e) for e in evidence if e.get("evidence_id") in set(payload.evidence_ids)],
        evidence_count=len(evidence))


def record(task, payload, folder, ledger=failure_log.LEDGER):
    """의견을 저장하고 실패 장부에 한 줄을 덧붙인다. 저장한 의견을 돌려준다."""
    item = build(task, payload)
    summary = {kind: [item["run"]["request_id"]] for kind in item["kinds"]}
    failure_log.record("feedback", SET_NAME, item["run"]["request_id"], [item], summary,
                       Path(folder) / item["feedback_id"], ledger=ledger)
    return item


def listed(request_id, ledger=failure_log.LEDGER):
    """그 질문에 남긴 의견들(장부 순서). 장부에 적힌 기록 파일을 읽는다."""
    import json
    out = []
    for line in failure_log.read_ledger(ledger):
        if line.get("kind") == "feedback" and line.get("label") == request_id:
            path = Path(line["file"])
            if path.is_file():
                out += json.loads(path.read_text(encoding="utf-8")).get("failures", [])
    return out
