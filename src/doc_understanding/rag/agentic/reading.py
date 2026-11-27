# 역할: 근거 조각이 속한 표를 통째로 근거에 넣는다(조각 몇 개가 아니라 단위 전체를 읽고 답하게 한다).
"""읽기 범위 넓히기.

검색과 선별은 질문과 가장 가까운 칸 몇 개만 남긴다. 값 하나를 찾는 질문에는 충분하지만, 표 전체를 봐야 하는
질문(전부 나열, 조건을 대입한 판단, 비교)에서는 나머지 행이 빠진 채로 답하게 된다. 여기서는 선별된 근거가
가리키는 표의 칸을 모두 근거로 복원한다. 값은 저장된 근거 출처에서 그대로 읽고, 새로 만들거나 고치지 않는다.

질문 문구나 문서 종류로 판단하지 않는다. 넓힐지 말지는 그래프(graph.py)가 정하고, 여기는 넓히는 일만 한다.
본문 근거는 그대로 둔다(본문을 쪽·절 단위로 넓히는 것은 아직 없다).
"""
import json

from doc_understanding.rag import repository
from doc_understanding.rag.evidence.source import as_source

# 한 번의 답변 요청에 싣는 양의 상한. 표가 너무 크면 그 표는 넓히지 않고 선별된 칸만 둔다(잘라서 일부만 넣지 않는다:
# 일부만 넣으면 '전체를 읽었다'는 전제가 깨진다).
MAX_CELLS_PER_TABLE = 150
MAX_BYTES = 36000


def size(items):
    """답변 요청에 실릴 크기의 어림값: 근거의 원문·경로 글자 바이트 수."""
    return sum(len(json.dumps([e.get("value_raw"), e.get("row_path"), e.get("column_path")], ensure_ascii=False)
                   .encode("utf8")) for e in items)


def expand_units(evidence, *, load=None, max_cells_per_table=MAX_CELLS_PER_TABLE, max_bytes=MAX_BYTES):
    """선별된 근거가 속한 표의 모든 칸을 근거로 넣고 E1..로 다시 번호를 매긴다.

    반환: (근거 목록, 기록). 기록에는 표마다 넓혔는지와 넓히지 않은 이유(TABLE_TOO_LARGE, BUDGET), 근거로 쓸 수 없어
    빠진 칸 수(withheld)가 남는다.
    순서: 원래 근거 순서를 지키되, 표가 처음 나오는 자리에 그 표의 칸을 표 순서대로 넣는다.
    """
    load = load or (lambda rid: repository.evidence_source(rid))  # 호출 때 찾는다(테스트·교체 가능)
    sources, expanded, records = {}, {}, []
    budget = max_bytes - size(evidence)
    for e in evidence:
        key = (e.get("run_id"), e.get("table_id"))
        if not e.get("table_id") or key in expanded:
            continue
        rid, table_id = key
        if rid not in sources:
            sources[rid] = as_source(load(rid))
        source = sources[rid]
        nested = source.nested_context(table_id)
        stored = source.cells(table_id)
        cells = [dict(c, run_id=rid, **(dict(nested_context=nested) if nested else {})) for c in stored if c["lookup_eligible"]]
        # 경로가 확정되지 않아 근거로 쓸 수 없는 칸은 넣지 않는다. 다만 그런 칸이 있으면 '표 전체를 읽었다'고 할 수 없으므로
        # 수를 남긴다(내용을 담는 칸은 안쪽 표·문장으로 따로 다뤄지는 중복이라 세지 않는다).
        withheld = sum(1 for c in stored if not c["lookup_eligible"] and not c.get("container_duplicate"))
        present = {x.get("cell_id") for x in evidence if x.get("table_id") == table_id and x.get("run_id") == rid}
        added = [c for c in cells if c["cell_id"] not in present]
        reason = ("TABLE_TOO_LARGE" if len(cells) > max_cells_per_table
                  else "BUDGET" if size(added) > budget else None)
        if reason is None:
            budget -= size(added)
        expanded[key] = None if reason else cells
        records.append(dict(run_id=rid, table_id=table_id, cells=len(cells), selected=len(present),
                            expanded=reason is None, reason=reason, withheld=withheld))
    out, placed = [], set()
    for e in evidence:
        key = (e.get("run_id"), e.get("table_id"))
        cells = expanded.get(key) if e.get("table_id") else None
        if cells is None:
            out.append(dict(e))
            continue
        if key in placed:
            continue
        placed.add(key)
        # 표 순서대로 넣는다. 선별된 칸은 선별 때의 항목을 그대로 쓴다(같은 칸이 두 번 들어가지 않는다).
        chosen = {x["cell_id"]: x for x in evidence if x.get("table_id") == e["table_id"] and x.get("run_id") == e.get("run_id")}
        for c in cells:
            out.append(dict(chosen.pop(c["cell_id"], c)))
        out.extend(dict(x) for x in chosen.values())  # 조회 불가로 표시된 칸이 선별에 있었으면 뒤에 그대로 둔다.
    for i, e in enumerate(out, 1):
        e["evidence_id"] = f"E{i}"
    return out, dict(before=len(evidence), after=len(out), tables=records)
