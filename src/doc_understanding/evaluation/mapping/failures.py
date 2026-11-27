# 역할: 매핑 채점에서 틀리거나 빠진 셀을 한 건씩 기록으로 만든다(문서·쪽·표·셀, 파싱된 경로와 기대 경로, 실패 종류).
"""실패 장부(매핑). 셀 보기 데이터와 테스트셋만 읽는다.

실패 종류
- wrong_confirmed: 근거로 쓰이는데 경로가 기대와 다르다(틀린 답 위험)
- withheld: 값인데 근거에서 빠졌다(path_right=True면 경로는 맞는데 보류)
- false_value: 라벨·머리글이어야 하는데 근거 값으로 쓰인다
- role_wrong: 역할이 기대와 다르다(위 세 가지에 해당하지 않는 경우)
- container_missing: 내용을 담는 칸인데 그렇게 인식하지 못했다 / container_as_value: 담는 칸 원문이 값으로 쓰인다
- content_mismatch: 담는 칸으로 인식했지만 칸 안 항목(순서·구성)이 기대와 다르다
- cell_mismatch: 같은 좌표의 원문이 테스트셋과 달라 채점하지 못했다
- parent_wrong·context_missing(표 단위): 칸 안에 담긴 표의 담긴 위치가 다르다 / 근거에 담은 칸 라벨이 없다
"""
from doc_understanding.evaluation.mapping.dataset import content_refs, table_id
from doc_understanding.evaluation.mapping.score import score_table


def _clean(path):
    return [" ".join(x.split()) for x in path]


def table_failures(set_name, expected, view):
    rows, _ = score_table(expected, view)
    grid = {(c["r"], c["c"]): c for c in view["cells"]}
    wanted = {(c.r, c.c): c for c in expected.cells}
    base = dict(set=set_name, doc=expected.doc, table=expected.table, page=view["page"],
                patterns=list(expected.patterns), table_codes=view["table_codes"])
    out = []
    for row in rows:
        e, a = wanted[(row["r"], row["c"])], grid.get((row["r"], row["c"]))
        kind = None
        if row.get("status") == "cell_mismatch":
            kind = "cell_mismatch"
        elif row.get("wrong_confirmed"):
            kind = "wrong_confirmed"
        elif row.get("target") and not row["eligible"]:
            kind = "withheld"
        elif row.get("false_value"):
            kind = "false_value"
        elif row.get("container") and not row["container_found"]:
            kind = "container_missing"
        elif row.get("container_as_value"):
            kind = "container_as_value"
        elif row.get("container") and not row["content_exact"]:
            kind = "content_mismatch"
        elif row.get("role_correct") is False:
            kind = "role_wrong"
        if kind is None:
            continue
        out.append(dict(
            base, kind=kind, r=row["r"], c=row["c"], text=(a["text"] if a else e.text)[:200],
            expected_role=e.role, parsed_role=row.get("actual_role"),
            expected_row_path=list(e.row_path), expected_column_path=list(e.column_path),
            parsed_row_path=_clean(a["row_path"]) if a else [], parsed_column_path=_clean(a["column_path"]) if a else [],
            path_right=bool(row.get("path_right")), blockers=row.get("blockers") or [], codes=a["codes"] if a else [],
            note=e.note, reviewer=e.reviewer))
        if kind == "content_mismatch":
            out[-1].update(expected_content=[x.model_dump(exclude_none=True) for x in e.content],
                           parsed_content=[x.model_dump(exclude_none=True)
                                           for x in content_refs(a["container"]["items"])])
    if expected.parents:
        actual = [(p["table"].split("/", 1)[1], p["r"], p["c"]) for p in view.get("parents", [])]
        if actual != [(p.table, p.r, p.c) for p in expected.parents]:
            out.append(dict(base, kind="parent_wrong", r=None, c=None, text="",
                            expected_parents=[p.model_dump() for p in expected.parents], parsed_parents=actual))
        elif not view.get("context_carried"):
            out.append(dict(base, kind="context_missing", r=None, c=None, text=""))
    return out


def mapping_failures(data, views):
    """views: table_id → 셀 보기. 반환: 실패 기록 목록(표·셀 순서)."""
    out = []
    for t in data.tables:
        view = views.get(table_id(data.documents[t.doc], t.table))
        if view is None:
            out.append(dict(set=data.name, doc=t.doc, table=t.table, kind="table_missing", r=None, c=None, text=""))
            continue
        out += table_failures(data.name, t, view)
    return out


def summarize(failures):
    def count(key):
        totals = {}
        for f in failures:
            for value in (f.get(key) if isinstance(f.get(key), list) else [f.get(key)]):
                totals[value] = totals.get(value, 0) + 1
        return dict(sorted(totals.items(), key=lambda kv: -kv[1]))
    return dict(total=len(failures), by_kind=count("kind"), by_doc=count("doc"), by_pattern=count("patterns"),
                tables=len({(f["doc"], f["table"]) for f in failures}))
