# 역할: 규칙이 보류한 판정에 한해 모델이 후보 중 하나를 고르고, 별도 Judge가 동의할 때만 채택한다.
"""Model-assisted selection (ADR-078 2단계). 모델은 나열된 후보 ID만 고를 수 있다.

실패·기권·Judge 불일치는 모두 보류로 남기며 이유를 기록한다. 채택해도 사람 승인 전 후보다.
"""
import hashlib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from doc_understanding.agents.structure_review.prompts import JUDGE, SELECTOR
from doc_understanding.runtime.structured_call import structured_call

KEEP = {"layout": "keep_unresolved", "scope": "none"}


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    choice_id: str = Field(min_length=1, max_length=200)
    evidence_ids: list[str] = Field(max_length=40)
    reason: str = Field(max_length=600)


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agree: bool
    reason: str = Field(max_length=600)


def layout_options(decision):
    """규칙이 만든 후보 배치(머리글 행 수·행별 라벨 깊이)를 선택지로 바꾼다. 모델은 이 목록 밖의 배치를 만들 수 없다."""
    options = []
    for i, c in enumerate(c for c in decision.candidates if "header_rows" in c):
        depths = c.get("row_stub_depths")
        body = depths[c["header_rows"]:] if depths else None
        labels = (f"each data row starts with {body[0]} label cell(s)" if body and len(set(body)) == 1
                  else "the number of leading label cells differs per data row (see row_label_depths)" if body
                  else f"each data row starts with {c['stub_columns']} label cell(s)")
        meaning = (f"no header row: every row including the first is data; {labels} followed by its content"
                   if c["header_rows"] == 0 else
                   f"the first {c['header_rows']} row(s) are column headers; {labels} followed by values")
        option = dict(id=f"layout_{i + 1}", header_rows=c["header_rows"], meaning=meaning)
        if body:
            option["row_label_depths"] = list(body)
        options.append((option, c))
    return options


def options_for(decision):
    if decision.aspect == "layout":
        derived = [o for o, _ in layout_options(decision)]
        return (derived or [dict(id="header1_stub1", header_rows=1, stub_columns=1,
                                 meaning="first row is the column header, first column holds row labels")]) + [
            dict(id=KEEP["layout"], meaning="evidence is insufficient; keep paths unresolved")]
    return ([dict(id=c["content_id"], text_raw=c["text_raw"]) for c in decision.candidates]
            + [dict(id=KEEP["scope"], meaning="no heading clearly governs this table")])


def layout_context(table):
    cells = [dict(cell_id=c.cell_id, row=c.row_index, column=c.column_index,
                  rowspan=c.rowspan, colspan=c.colspan, text=(c.original_text or "")[:200])
             for c in table.cells]
    return dict(table_id=table.table_id, page=table.page_number, cells=cells)


def resolve(decision, context, policy, ledger, output, *, allow_external, transport=None):
    """보류된 Decision 하나를 모델로 다시 판단한다. 반환값은 채택된 Decision 또는 이유가 붙은 보류."""
    if decision.status != "ambiguous":
        return decision
    options = options_for(decision)
    allowed = {o["id"] for o in options}
    known = set(decision.evidence_ids) | allowed | {c.get("cell_id") for c in context.get("cells", [])}
    user = dict(task=decision.aspect, rule_reason=decision.reason, supports=list(decision.supports),
                counters=list(decision.counters), options=options, context=context)
    kwargs = dict(allow_external=allow_external, ledger=ledger)
    if transport:
        kwargs["transport"] = transport
    # 표 ID(문서 해시 포함)는 폴더 이름으로 쓰기에 길고 ':'·'/'가 있다(Windows 경로 한도·금지 문자).
    # 표 위치와 ID 해시 앞부분으로 짧은 이름을 만든다.
    name = "_".join(decision.table_id.split("/")[-2:]) + "-" + hashlib.sha256(decision.table_id.encode()).hexdigest()[:10]
    base = Path(output) / name / decision.aspect

    def held(reason):
        return decision.model_copy(update={"reason": reason})

    try:
        picked, _ = structured_call("structure_selection", SELECTOR, user, Selection, policy,
                                    base / "select", **kwargs)
    except (RuntimeError, ValueError) as error:
        return held("MODEL_SELECTION_FAILED:" + str(error))
    if picked.choice_id not in allowed or not set(picked.evidence_ids) <= known:
        return held("MODEL_SELECTION_OUT_OF_OPTIONS")
    if picked.choice_id == KEEP[decision.aspect]:
        return held("MODEL_ABSTAINED")
    try:
        verdict, _ = structured_call("structure_judgment", JUDGE, dict(user, proposed=picked.model_dump()),
                                     Verdict, policy, base / "judge", **kwargs)
    except (RuntimeError, ValueError) as error:
        return held("MODEL_JUDGE_FAILED:" + str(error))
    if not verdict.agree:
        return held("MODEL_JUDGE_DISAGREED")
    option = next(o for o in options if o["id"] == picked.choice_id)
    if decision.aspect == "layout":
        # 선택지는 규칙이 만든 후보 배치와 1:1이다. 채택하는 것은 그 후보 자체다.
        candidate = next((c for o, c in layout_options(decision) if o["id"] == picked.choice_id), None)
        choice = dict(candidate) if candidate else {"header_rows": option["header_rows"],
                                                    "stub_columns": option["stub_columns"]}
    else:
        choice = {"content_id": option["id"], "text_raw": option["text_raw"]}
    return decision.model_copy(update=dict(status="decided", reason="MODEL_SELECTED_JUDGE_AGREED",
                                           choice=choice, source="model",
                                           evidence_ids=tuple(picked.evidence_ids)))
