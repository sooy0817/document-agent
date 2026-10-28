"""Description review adapter reusing immutable source ledger and approval gates."""

import json
import hashlib
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from doc_understanding.human_review.store import ReviewStore
from doc_understanding.agent_contracts.models import StructureProposalBundle
from doc_understanding.agent_contracts.validation import validate_bundle
from .models import SemanticDescriptionBundle, validate_descriptions
from .guidance import supplement


class DescriptionReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewer: str = Field(min_length=1)
    expected_revision: int = Field(ge=0)
    target_id: str
    verdict: str
    note: str = ""
    proposal: dict
    descriptions: dict


class DescriptionStore(ReviewStore):
    def case(self, key):
        result = super().case(key)
        path = self.paths[key].with_name("descriptions.json")
        original = SemanticDescriptionBundle.model_validate_json(path.read_text(encoding="utf-8"))
        latest = next(
            (
                x["descriptions_after"]
                for x in reversed(result["events"])
                if "descriptions_after" in x
            ),
            original.model_dump(mode="json"),
        )
        result["descriptions"] = latest
        result["metadata"] = json.loads(
            self.paths[key].with_name("metadata.json").read_text(encoding="utf-8")
        )
        p = StructureProposalBundle.model_validate(result["current_proposal"])
        bundle = SemanticDescriptionBundle.model_validate(latest)
        result["description_validation"] = validate_descriptions(
            self._input(key).evidence, p, bundle
        )
        # Description-only changes also invalidate all active approvals.
        last_change = max(
            (x["revision"] for x in result["events"] if x.get("description_changed")), default=0
        )
        result["scopes"] = {
            k: v for k, v in result["scopes"].items() if v["review"]["revision"] >= last_change
        }
        return result

    def _append(self, key, c, event):
        if event.get("kind") == "review":
            path = (
                Path(__file__).resolve().parents[3]
                / "reports/semantic-human-review/review-pack.json"
            )
            if path.exists():
                pack = json.loads(path.read_text(encoding="utf-8"))
                question = next(
                    (
                        x
                        for x in pack["items"]
                        if x["case_id"] == key and x["description_id"] == event["target_id"]
                    ),
                    None,
                )
                event["review_context"] = dict(
                    pack_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    question=question,
                    protocol="source_comparison_with_visible_agent_proposal",
                    independent_gold_confirmed=False,
                    evaluation_use="development_candidate_pending_separate_gold_confirmation",
                )
                event["review_context"]["supplement"] = supplement(
                    Path(__file__).resolve().parents[3], key, event["target_id"]
                )
        bundle = event.get("descriptions_after", c.get("descriptions"))
        if bundle:
            event["description_sha256"] = SemanticDescriptionBundle.model_validate(bundle).digest()
        return super()._append(key, c, event)

    def review_description(self, key, r):
        with self.lock:
            c = self._check(key, r.expected_revision)
            can = self._input(key)
            if not r.reviewer.strip():
                raise ValueError("검토자 이름을 입력하세요.")
            if r.verdict not in ("correct", "incorrect", "defer"):
                raise ValueError("판단을 선택하세요.")
            before = StructureProposalBundle.model_validate(c["current_proposal"])
            p = StructureProposalBundle.model_validate(r.proposal)
            bd = SemanticDescriptionBundle.model_validate(c["descriptions"])
            d = SemanticDescriptionBundle.model_validate(r.descriptions)
            if p.model_dump(
                exclude={
                    "nodes",
                    "relations",
                    "operations",
                    "trees",
                    "bindings",
                    "ambiguities",
                    "coverage",
                }
            ) != before.model_dump(
                exclude={
                    "nodes",
                    "relations",
                    "operations",
                    "trees",
                    "bindings",
                    "ambiguities",
                    "coverage",
                }
            ):
                raise ValueError("실행 출처는 수정할 수 없습니다.")
            if d.origin != bd.origin or d.interpreter != bd.interpreter:
                raise ValueError("설명 생성 출처는 변경할 수 없습니다.")
            if d.evidence_sha256 != bd.evidence_sha256 or d.proposal_sha256 not in (
                bd.proposal_sha256,
                p.digest(),
            ):
                raise ValueError("설명 근거 버전이 다릅니다.")
            d = d.model_copy(update={"proposal_sha256": p.digest()})
            selected = next((x for x in d.descriptions if x.description_id == r.target_id), None)
            if not selected:
                raise ValueError("설명 ID가 없습니다.")
            valid = validate_bundle(can.evidence, p)
            cross = validate_descriptions(can.evidence, p, d)
            if not valid.admissible or not cross["structurally_consistent"]:
                raise ValueError(
                    "구조 또는 설명 assertion 충돌이 있습니다. 관계와 설명 주장을 함께 수정하세요."
                )
            changed = p != before or d != bd
            if changed and r.verdict != "incorrect":
                raise ValueError("수정 내용은 수정 필요 판단으로 저장하세요.")
            if r.verdict in ("incorrect", "defer") and not r.note.strip():
                raise ValueError("수정·보류 사유가 필요합니다.")
            status = (
                "corrected"
                if changed
                else {"correct": "accepted", "incorrect": "rejected", "defer": "deferred"}[
                    r.verdict
                ]
            )
            ev = {x.evidence_id: x for x in can.evidence.items}
            return self._append(
                key,
                c,
                dict(
                    kind="review",
                    reviewer=r.reviewer.strip(),
                    target_id=r.target_id,
                    scope="description",
                    verdict=r.verdict,
                    status=status,
                    note=r.note,
                    evidence=[ev[x].model_dump(mode="json") for x in selected.evidence_ids],
                    before=before.model_dump(mode="json"),
                    after=p.model_dump(mode="json"),
                    before_sha256=before.digest(),
                    after_sha256=p.digest(),
                    descriptions_before=bd.model_dump(mode="json"),
                    descriptions_after=d.model_dump(mode="json"),
                    description_changed=d != bd,
                    validation=valid.model_dump(mode="json"),
                    description_validation=cross,
                    complete_structure_attested=False,
                ),
            )
