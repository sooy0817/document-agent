"""Local, append-only, revision-bound human review. Not a fact publisher."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from doc_understanding.agents.contracts.models import CanonicalSnapshot, StructureProposalBundle
from doc_understanding.agents.contracts.validation import validate_bundle


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewer: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=0)
    target_id: str | None = None
    verdict: Literal["correct", "incorrect", "defer"]
    evidence_ids: list[str] = Field(min_length=1)
    note: str = Field(default="", max_length=10000)
    after: dict
    complete_structure_attested: bool = False

    @field_validator("reviewer")
    @classmethod
    def identity(cls, value):
        if not value.strip():
            raise ValueError("검토자를 입력하세요.")
        return value.strip()


class GateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewer: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=0)
    action: Literal["candidate", "gold_final", "publication"]
    note: str = Field(min_length=1, max_length=10000)

    @field_validator("reviewer", "note")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("담당자와 승인 사유를 입력하세요.")
        return value.strip()


class ReviewStore:
    def __init__(self, cases: Path, output: Path):
        self.paths = {p.parent.name: p for p in cases.glob("*/canonical.json")}
        self.output = output
        output.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()

    def _input(self, key):
        if key not in self.paths:
            raise KeyError("알 수 없는 사례")
        return CanonicalSnapshot.model_validate_json(self.paths[key].read_text(encoding="utf-8"))

    def _events(self, key):
        self._input(key)
        p = self.output / (key + ".json")
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []

    def case(self, key):
        canonical = self._input(key)
        events = self._events(key)
        gates = dict(gold_candidate="pending", gold_final="pending", publication="pending")
        current = canonical.proposal.model_dump(mode="json")
        latest = None
        scopes = {}
        for event in events:
            if event["canonical_sha256"] != canonical.digest():
                raise ValueError("원본 버전이 변경되었습니다. 별도 검토 이력이 필요합니다.")
            if event["kind"] == "review":
                if current != event["after"]:
                    scopes = {}  # Conservatively invalidate all approvals on structural edits.
                current = event["after"]
                latest = event
                gates = dict(gold_candidate="pending", gold_final="pending", publication="pending")
                scopes[event["target_id"] or "__whole__"] = {"review": event, "gates": gates}
            else:
                gates[
                    {
                        "candidate": "gold_candidate",
                        "gold_final": "gold_final",
                        "publication": "publication",
                    }[event["action"]]
                ] = "approved"
        return dict(
            case_id=key,
            canonical=canonical.model_dump(mode="json"),
            current_proposal=current,
            revision=len(events),
            events=events,
            gates=gates,
            latest_review=latest,
            scopes=scopes,
            identity_mode="self_declared_local",
            fact_published=False,
        )

    def _check(self, key, expected):
        c = self.case(key)
        if c["revision"] != expected:
            raise ValueError("다른 저장이 있습니다. 새로 불러온 후 검토하세요.")
        for a in self._input(key).evidence.artifacts:
            path = Path(a.uri)
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != a.sha256:
                raise ValueError("원본 근거 파일의 해시가 다릅니다.")
        return c

    def _append(self, key, c, event):
        event.update(
            revision=c["revision"] + 1,
            timestamp=datetime.now(timezone.utc).isoformat(),
            canonical_sha256=self._input(key).digest(),
            fact_published=False,
        )
        events = c["events"] + [event]
        target = self.output / (key + ".json")
        temp = target.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as f:
            json.dump(events, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, target)
        return event

    def review(self, key, r: ReviewInput):
        with self.lock:
            c = self._check(key, r.expected_revision)
            can = self._input(key)
            after = StructureProposalBundle.model_validate(r.after)
            before = StructureProposalBundle.model_validate(c["current_proposal"])
            if (
                after.proposal_id,
                after.evidence_sha256,
                after.mode,
                after.origin,
                after.interpreter_id,
                after.configuration_sha256,
            ) != (
                before.proposal_id,
                before.evidence_sha256,
                before.mode,
                before.origin,
                before.interpreter_id,
                before.configuration_sha256,
            ):
                raise ValueError("원본 실행 정보는 수정할 수 없습니다.")
            valid = validate_bundle(can.evidence, after)
            if not valid.admissible:
                raise ValueError(
                    "구조 검증 실패: "
                    + ", ".join(sorted({x.code for x in valid.checks if x.outcome == "fail"}))
                )
            ev = {x.evidence_id: x for x in can.evidence.items}
            if not set(r.evidence_ids) <= ev.keys():
                raise ValueError("알 수 없는 근거 ID")
            if r.target_id:
                old = {x.claim_id: x for x in before.relations}
                new = {x.claim_id: x for x in after.relations}
                if r.target_id not in old and r.target_id not in new:
                    raise ValueError("검토할 관계가 없습니다.")
                bd = before.model_dump(mode="json")
                ad = after.model_dump(mode="json")
                # Only selected claim and its existing operation may be edited in scoped review.
                allowed_ops = {
                    x.operation_id
                    for x in [old.get(r.target_id), new.get(r.target_id)]
                    if x and x.operation_id
                }
                for d in (bd, ad):
                    d["relations"] = [x for x in d["relations"] if x["claim_id"] != r.target_id]
                    d["operations"] = [
                        x for x in d["operations"] if x["operation_id"] not in allowed_ops
                    ]
                if bd != ad:
                    raise ValueError("선택한 관계 외의 수정은 전체 구조 검토에서 진행하세요.")
                # A shared operation would change other claims, requiring whole-structure scope.
                if before.operations != after.operations and any(
                    x.claim_id != r.target_id and x.operation_id in allowed_ops
                    for x in before.relations
                ):
                    raise ValueError("공유 연산 변경은 전체 구조 검토가 필요합니다.")
            changed = before != after
            if r.verdict != "incorrect" and changed:
                raise ValueError("수정한 구조는 틀림·수정으로 저장하세요.")
            if r.verdict in ("incorrect", "defer") and not r.note.strip():
                raise ValueError("수정 또는 판단 보류 사유를 적어주세요.")
            status = (
                "corrected"
                if r.verdict == "incorrect" and changed
                else {"correct": "accepted", "incorrect": "rejected", "defer": "deferred"}[
                    r.verdict
                ]
            )
            return self._append(
                key,
                c,
                dict(
                    kind="review",
                    reviewer=r.reviewer,
                    target_id=r.target_id,
                    scope="relation" if r.target_id else "whole_structure",
                    verdict=r.verdict,
                    status=status,
                    note=r.note,
                    evidence=[ev[x].model_dump(mode="json") for x in r.evidence_ids],
                    evidence_sha256=can.evidence.digest(),
                    before=before.model_dump(mode="json"),
                    after=after.model_dump(mode="json"),
                    before_sha256=before.digest(),
                    after_sha256=after.digest(),
                    validation=valid.model_dump(mode="json"),
                    complete_structure_attested=r.complete_structure_attested,
                ),
            )

    def gate(self, key, r: GateInput):
        with self.lock:
            c = self._check(key, r.expected_revision)
            review = c["latest_review"]
            if not review or review["status"] not in ("accepted", "corrected"):
                raise ValueError("맞음 또는 수정 완료로 저장된 검토만 승인할 수 있습니다.")
            if r.action == "candidate":
                if c["gates"]["gold_candidate"] == "approved":
                    raise ValueError("이미 후보 승인되었습니다.")
                if review["scope"] == "whole_structure":
                    p = review["after"]
                    if (
                        not review["complete_structure_attested"]
                        or p["ambiguities"]
                        or any(x["disposition"] == "unresolved" for x in p["coverage"])
                        or any(x["status"] == "unresolved" for x in p["bindings"])
                    ):
                        raise ValueError(
                            "전체 구조 후보는 전체 확인과 미결정 항목 해소가 필요합니다. 관계 단위 승인은 별도 가능합니다."
                        )
            elif r.action == "gold_final":
                if c["gates"]["gold_candidate"] != "approved":
                    raise ValueError("Gold 후보 승인이 먼저 필요합니다.")
                candidate = next(x for x in reversed(c["events"]) if x.get("action") == "candidate")
                if r.reviewer in (review["reviewer"], candidate["reviewer"]):
                    raise ValueError("Gold 확정은 다른 검토자가 수행해야 합니다.")
                if c["gates"]["gold_final"] == "approved":
                    raise ValueError("이미 최종 확정되었습니다.")
            else:
                if c["gates"]["gold_final"] != "approved":
                    raise ValueError("Gold 최종 확정 이후 별도 발행 승인만 기록할 수 있습니다.")
                if c["gates"]["publication"] == "approved":
                    raise ValueError("이미 발행 승인 기록이 있습니다.")
            return self._append(
                key,
                c,
                dict(
                    kind="gate",
                    action=r.action,
                    reviewer=r.reviewer,
                    note=r.note,
                    review_revision=review["revision"],
                    proposal_sha256=review["after_sha256"],
                    scope=review["scope"],
                    target_id=review["target_id"],
                ),
            )

    def candidates(self):
        result = []
        for key in self.paths:
            c = self.case(key)
            for scope in c["scopes"].values():
                if scope["gates"]["gold_candidate"] == "approved":
                    r = scope["review"]
                    result.append(
                        dict(
                            case_id=key,
                            scope=r["scope"],
                            target_id=r["target_id"],
                            review=r,
                            gates=scope["gates"],
                            fact_published=False,
                        )
                    )
        return result
