# 역할: RAG 평가셋 계약. 정답 위치는 셀 ID가 아니라 문서·쪽·원문 일부로 고정해 파서 버전이 바뀌어도 쓸 수 있게 한다.
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

ItemType = Literal["lookup", "interval", "hierarchical_row", "nested_scope", "key_value", "body",
                   "multi_document", "ambiguous", "unanswerable", "notation_variant", "table_and_body",
                   # 값 하나 찾기 밖의 질문 유형(rag-general-v1): 조건 대입 판단, 가능 여부, 절차, 전부 나열, 여러 근거 종합.
                   "applied_condition", "eligibility", "procedure", "enumeration", "synthesis"]

Term = Annotated[str, Field(min_length=1)]
TermGroup = Annotated[list[Term], Field(min_length=1)]


class EvidenceLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc: str = Field(min_length=1)
    page: int = Field(ge=1)
    text: str = Field(min_length=1)
    # 선택: 같은 표에서 이 셀의 행을 덮는 다른 셀의 원문에 이 라벨이 있어야 한다(값 원문이 같은 가지 구분용).
    row_label: str | None = Field(default=None, min_length=1)


class Anchor(EvidenceLocation):
    # Explicit equivalent-location candidates within this same document only.
    equivalent_locations: list[EvidenceLocation] = []
    # 이 위치로 조건부 답을 할 때 문장에 필요한 문구들. 그룹마다 한 표현이 있어야 한다.
    conditional_terms: list[TermGroup] = []
    value_aliases: list[Term] = []


class EvalItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    type: ItemType
    question: str = Field(min_length=1)
    gold: list[Anchor] = []
    expected: list[str] = []
    expected_equivalents: dict[str, list[Term]] = {}
    # 선택 값·답변 문장에 나오면 오답인 문자열(비슷하지만 다른 행·문맥의 값). 기대 문자열이 우연히 포함되는 경우를 막는다.
    forbidden: list[str] = []
    answerable: bool = True
    ambiguous: bool = False
    alternative_gold: list[Anchor] = []
    clarification_terms: list[TermGroup] = []
    require_selected_evidence: bool = False
    note: str | None = None


class EvalSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    status: Literal["draft", "reviewed"]
    reviewed_by_human: bool
    note: str = ""
    documents: dict[str, str]
    items: list[EvalItem]

    def check(self):
        """ID 중복, 알 수 없는 문서 키, 답할 수 있는데 정답 위치가 없는 항목을 거부한다."""
        reasons = []
        ids = [i.id for i in self.items]
        if len(ids) != len(set(ids)):
            reasons.append("DUPLICATE_ITEM_ID")
        for item in self.items:
            for anchor in item.gold + item.alternative_gold:
                if anchor.doc not in self.documents:
                    reasons.append(f"UNKNOWN_DOCUMENT:{item.id}")
                if any(a.doc != anchor.doc for a in anchor.equivalent_locations):
                    reasons.append(f"EQUIVALENT_LOCATION_DIFFERENT_DOCUMENT:{item.id}")
            if set(item.expected_equivalents)-set(item.expected):
                reasons.append(f"EQUIVALENT_TERM_WITHOUT_EXPECTATION:{item.id}")
            if item.answerable and not item.ambiguous and not item.gold:
                reasons.append(f"ANSWERABLE_WITHOUT_GOLD:{item.id}")
            if item.answerable and not item.ambiguous and not item.expected:
                reasons.append(f"ANSWERABLE_WITHOUT_EXPECTED:{item.id}")
            if not item.answerable and (item.gold or item.expected):
                reasons.append(f"UNANSWERABLE_WITH_GOLD:{item.id}")
        return sorted(set(reasons))


def load(path):
    data = EvalSet.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
    problems = data.check()
    if problems:
        raise ValueError("INVALID_EVAL_SET:" + ",".join(problems))
    return data
