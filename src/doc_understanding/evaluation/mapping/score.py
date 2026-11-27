# 역할: 매핑 테스트셋의 기대 역할·경로와 구조 분석 결과의 셀 보기(rag.evidence.inspect가 만든 데이터)를 비교해
# 셀·표·유형별 지표를 낸다. 평가기는 결과 데이터만 읽고 RAG·파서 런타임을 import하지 않는다.
"""지표(개발용 초안 기준이며 일반화 성능 주장이 아니다)

- role_accuracy: ignore가 아닌 셀 중 분석기 역할이 기대 역할과 같은 비율
- 대상 값 셀(target): 기대 역할이 value이고 행·열 경로 중 하나라도 있는 셀
  - answerable: 근거로 쓰일 수 있는 비율(lookup_eligible)
  - exact: 근거로 쓰일 수 있고 행·열 경로가 기대와 같다(공백 무시)
  - wrong_confirmed: 근거로 쓰일 수 있는데 경로가 다르다(틀린 답을 확정할 위험)
  - path_right_but_withheld: 경로는 맞는데 미확정 등으로 근거에서 빠졌다
- false_value: 값이 아니어야 할 셀이 근거로 쓰일 수 있게 된 수
- 담는 칸(container): container_found(담는 칸으로 인식), content_exact(안쪽 항목 순서·구성 일치),
  container_as_value(이어 붙인 원문이 값 근거로 쓰임 — 안쪽 표·문장과 중복)
- 담긴 표: parent_correct(담긴 위치 일치), context_carried(근거가 담은 칸의 라벨을 함께 싣는가)
- cell_mismatch: 셀 누락·원문 불일치. 전체 분모에 남기고 실패로 계산한다.
- exact_matched: 원문이 일치한 대상만의 조건부 경로 정확도. 전체 exact를 대신하지 않는다.
"""
from collections import Counter

from doc_understanding.evaluation.mapping.dataset import compact, content_refs, same_path, table_id

METRIC_VERSION = "mapping/2-full-denominator"


def score_table(expected, view):
    grid = {(c["r"], c["c"]): c for c in view["cells"]} if view is not None else {}
    rows, blockers = [], Counter()
    for e in expected.cells:
        actual = grid.get((e.r, e.c))
        row = dict(r=e.r, c=e.c, expected_role=e.role, reviewed=e.reviewed, reviewer=e.reviewer)
        # 분모는 기대값에서 정한다. 출력이 사라져도 평가 대상은 줄지 않는다.
        if e.role != "ignore":
            row["role_correct"] = False
        if e.role == "value" and (e.row_path or e.column_path):
            row.update(target=True, eligible=False, exact=False)
        if e.role == "container":
            row.update(container=True, container_found=False, content_exact=False, container_as_value=False)
        if actual is None or compact(actual["text"]) != e.text:
            rows.append(dict(row, status="missing_table" if view is None else "cell_mismatch",
                             mismatch_kind="missing_cell" if actual is None else "text_mismatch"))
            continue
        actual_role = "container" if actual.get("container") else actual["role"]
        row.update(actual_role=actual_role, eligible=actual["eligible"], blockers=actual["blockers"])
        if e.role != "ignore":
            row["role_correct"] = actual_role == e.role
        if e.role == "container":
            found = bool(actual.get("container"))
            row.update(container=True, container_found=found,
                       content_exact=found and content_refs(actual["container"]["items"]) == e.content,
                       container_as_value=actual["eligible"])
        if e.role == "value" and (e.row_path or e.column_path):
            right = same_path(actual["row_path"], e.row_path) and same_path(actual["column_path"], e.column_path)
            row.update(target=True, path_right=right, exact=actual["eligible"] and right,
                       wrong_confirmed=actual["eligible"] and not right,
                       path_right_but_withheld=right and not actual["eligible"])
            if not actual["eligible"]:
                blockers.update(actual["blockers"] or ["UNKNOWN"])
        elif e.role in ("row_label", "column_header", "corner") and actual["eligible"]:
            row["false_value"] = True
        rows.append(row)
    return rows, blockers


def summarize(rows):
    def rate(key, pool):
        values = [bool(r.get(key)) for r in pool]
        return dict(n=len(values), rate=round(sum(values) / len(values), 3) if values else None)
    targets = [r for r in rows if r.get("target")]
    roled = [r for r in rows if "role_correct" in r]
    containers = [r for r in rows if r.get("container")]
    matched = [r for r in rows if not r.get("status")]
    return dict(cells=len(rows), targets=len(targets), containers=len(containers),
                container_found=rate("container_found", containers), content_exact=rate("content_exact", containers),
                container_as_value=sum(bool(r.get("container_as_value")) for r in containers),
                role_accuracy=rate("role_correct", roled),
                answerable=rate("eligible", targets), exact=rate("exact", targets),
                wrong_confirmed=sum(bool(r.get("wrong_confirmed")) for r in targets),
                path_right_but_withheld=sum(bool(r.get("path_right_but_withheld")) for r in targets),
                false_value=sum(bool(r.get("false_value")) for r in rows),
                cell_mismatch=sum(r.get("status") == "cell_mismatch" for r in rows),
                missing_table_cells=sum(r.get("status") == "missing_table" for r in rows),
                source_match=rate("matched", [dict(matched=not r.get("status")) for r in rows]),
                exact_matched=rate("exact", [r for r in matched if r.get("target")]),
                reviewed=sum(bool(r.get("reviewed")) for r in rows),
                reviewed_by_human=sum(r.get("reviewer") == "human" for r in rows if r.get("reviewed")))


def score(data, views):
    """views: table_id → 셀 보기(코퍼스 문서 또는 후보 실행 문서에서 만든 것)."""
    tables, all_rows, by_pattern, blockers, nested = [], [], {}, Counter(), []
    for t in data.tables:
        view = views.get(table_id(data.documents[t.doc], t.table))
        rows, b = score_table(t, view)
        blockers.update(b)
        if t.parents:
            actual = [(p["table"].split("/", 1)[1], p["r"], p["c"]) for p in (view or {}).get("parents", [])]
            nested.append(dict(table=t.table, parent_correct=actual == [(p.table, p.r, p.c) for p in t.parents],
                               context_carried=bool((view or {}).get("context_carried"))))
        all_rows += rows
        for p in t.patterns:
            by_pattern.setdefault(p, []).extend(rows)
        tables.append(dict(doc=t.doc, table=t.table, patterns=t.patterns, missing=view is None,
                           summary=summarize(rows), cells=rows))
    def nested_rate(key):
        return dict(n=len(nested), rate=round(sum(x[key] for x in nested) / len(nested), 3) if nested else None)
    summary = dict(summarize(all_rows), expected_tables=len(tables), missing_tables=sum(t["missing"] for t in tables),
                   nested_tables=len(nested), parent_correct=nested_rate("parent_correct"),
                   context_carried=nested_rate("context_carried"))
    return dict(metric_version=METRIC_VERSION, summary=summary, nested=nested, blockers=dict(blockers.most_common()),
                by_pattern={p: summarize(r) for p, r in sorted(by_pattern.items())}, tables=tables)
