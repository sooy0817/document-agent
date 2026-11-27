# 역할: 구조 분석 결과를 셀 단위로 펼쳐 '이 셀이 무엇으로 분류됐고, 근거로 쓰일 수 있는지, 아니면 왜 못 쓰는지'를 보여 준다.
"""셀 보기(읽기 전용). 매핑 검토 화면과 매핑 채점(evaluation.mapping, 이 결과 데이터만 읽음)이 같은 판정을 쓴다.

근거 사용 가능(eligible)은 rag.evidence.cells.cells_for_table의 lookup_eligible과 같은 규칙이다.
사용 불가 이유(blockers)는 그 규칙의 어느 조건에 걸렸는지와, 분석기가 남긴 모호 코드다.

내용을 담는 칸과 안쪽 표의 담긴 위치는 rag.evidence.containers가 정한다.
"""
import re

from doc_understanding.rag.evidence.cells import cells_for_table
from doc_understanding.rag.evidence.containers import containment, parents

# 표 전체에 걸리는 모호 코드: 셀에 직접 붙지 않아도 그 표의 모든 경로에 영향을 준다.
TABLE_WIDE = {"HEADER_REGION_AMBIGUOUS", "PANEL_SPLIT_AMBIGUOUS"}


def structure_of(doc, table_id):
    from doc_understanding.parser.relations.header_inheritance import effective_structure
    st = effective_structure(doc, table_id)
    pt = next(t for t in doc.physical_tables if t.table_id == table_id)
    return st, pt


def _compact(text):
    return re.sub(r"\s+", "", text or "")


def table_view(doc, table_id, info=None):
    info = info or containment(doc)
    st, pt = structure_of(doc, table_id)
    nodes = {n.node_id: n for n in st.row_tree + st.column_tree}
    roles = {r.cell_id: r for r in st.cell_roles}
    maps = {m.cell_id: m for m in st.cell_mappings}
    evidence = cells_for_table(doc, table_id)
    eligible = {c["cell_id"]: c["lookup_eligible"] for c in evidence}
    label_collisions = {c["cell_id"] for c in evidence if c.get("path_collision")}
    withheld = {c["cell_id"]: c["path_withheld"] for c in evidence if c.get("path_withheld")}
    by_cell = {}
    for a in st.ambiguities:
        for target in a.target_ids:
            by_cell.setdefault(target, []).append(a.code)
    table_codes = sorted({a.code for a in st.ambiguities if a.code in TABLE_WIDE})
    cells = []
    for c in pt.cells:
        if c.row_index is None or c.column_index is None:
            continue
        role = roles.get(c.cell_id)
        m = maps.get(c.cell_id)
        row = [nodes[n] for n in m.row_path] if m else []
        col = [nodes[n] for n in m.column_path] if m else []
        blockers = []
        if not m:
            blockers.append("NOT_A_VALUE_CELL" if role and role.role != "unknown" else "CELL_ROLE_UNRESOLVED")
        else:
            if not row:
                blockers.append("ROW_PATH_MISSING")
            if not col:
                blockers.append("COLUMN_PATH_MISSING")
            if any(not (n.label_raw or "").strip() for n in row + col):
                blockers.append("LABEL_UNAVAILABLE")
            if any(n.evidence_state == "unresolved" for n in row + col):
                blockers.append("PATH_NODE_UNRESOLVED")
            if c.status == "unresolved":
                blockers.append("PHYSICAL_UNRESOLVED")
            if c.cell_id in info["duplicates"]:
                blockers.append("CONTAINER_DUPLICATE")
            if c.cell_id in label_collisions:
                blockers.append("PATH_COLLISION")  # 다른 행의 칸과 라벨 글자 경로가 같다
            if c.cell_id in withheld:
                # 머리글 근거가 병합된 왼쪽 위 칸뿐이거나, 상위 항목이 문서의 번호 순서와 반대로 붙었다
                blockers.append(withheld[c.cell_id].upper())
            if m.evidence_state == "unresolved" and not blockers:
                blockers.append("PATH_COLLISION" if "PATH_COLLISION" in by_cell.get(c.cell_id, [])
                                else "MAPPING_UNRESOLVED")
        cells.append(dict(
            id=c.cell_id, r=c.row_index, c=c.column_index, rs=c.rowspan or 1, cs=c.colspan or 1,
            text=c.original_text or "", bbox=c.source_bbox.model_dump(),
            role=role.role if role else "unknown", role_state=role.evidence_state if role else "unresolved",
            row_path=[n.label_raw or "" for n in row], column_path=[n.label_raw or "" for n in col],
            row_candidates=[nodes[n].label_raw or "" for n in (m.row_candidates if m else ()) if n in nodes],
            column_candidates=[nodes[n].label_raw or "" for n in (m.column_candidates if m else ()) if n in nodes],
            mapping_state=m.evidence_state if m else None, physical_state=c.status,
            eligible=bool(eligible.get(c.cell_id)), blockers=[] if eligible.get(c.cell_id) else blockers,
            codes=sorted(set(by_cell.get(c.cell_id, []))),
            container=info["containers"].get(c.cell_id),
        ))
    panels = [p.model_dump(mode="json") for p in st.panels]
    chain = parents(doc, table_id, info)
    # 근거가 지금 실어 나르는 표 맥락(구조 검토 적용 범위·머리글 행 원문). 담긴 위치 라벨이 여기에 있는지 본다.
    carried = sorted({s["text_raw"] for e in evidence for s in e["scope_context"]}
                     | {x for e in evidence for x in e["table_context"]}
                     | {p["label"] for e in evidence for p in (e.get("nested_context") or {}).get("path", [])})
    labels = [x for p in chain for x in p["label"]]
    context_carried = bool(labels) and all(any(_compact(x) in _compact(y) for y in carried) for x in labels)
    return dict(table_id=table_id, page=pt.page_number, analysis_state=st.analysis_state,
                rows=1 + max((x["r"] + x["rs"] - 1 for x in cells), default=-1),
                columns=1 + max((x["c"] + x["cs"] - 1 for x in cells), default=-1),
                panels=panels, table_codes=table_codes, parents=chain, evidence_context=carried,
                context_carried=context_carried, issues=sorted({i.code for i in st.issues}), cells=cells)


def document_views(docs):
    """문서들의 모든 표 셀 보기. table_id → 셀 보기."""
    out = {}
    for doc in docs.values():
        info = containment(doc)
        out.update({t.table_id: table_view(doc, t.table_id, info) for t in doc.physical_tables})
    return out
