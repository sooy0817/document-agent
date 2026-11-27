# 역할: 판정을 적용해 검토 후보 CanonicalDocument를 만든다. 원본 IR·물리 표·원문은 그대로 두고 검증을 통과해야 한다.
"""Structure review pipeline (ADR-078): 규칙 판정 → (선택) 모델 선택·Judge → 분석기 재실행 → 문서 검증.

결과 문서는 원본과 해시가 달라 새 실행으로 저장된다. 원본 문서·실행은 바뀌지 않는다.
판정 근거는 표의 issues(STRUCTURE_REVIEW_LAYOUT·STRUCTURE_REVIEW_SCOPE)에 남고 스키마는 바뀌지 않는다.
"""
import json

from statistics import median
from uuid import uuid5

from doc_understanding.agents.structure_review.layout import decide_layout, grade
from doc_understanding.agents.structure_review.models import REVIEW_VERSION, Decision
from doc_understanding.agents.structure_review.scope import decide_scopes
from doc_understanding.agents.structure_review.selector import layout_context, resolve
from doc_understanding.domain.models import Issue
from doc_understanding.parser.assembly.document import numbered_view, validate_document
from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer
from doc_understanding.parser.structure.layout import TableLayout, derive_layout

LAYOUT_CODE = "STRUCTURE_REVIEW_LAYOUT"
SCOPE_CODE = "STRUCTURE_REVIEW_SCOPE"


def usable(table, physical):
    """답변 근거로 쓸 수 있는 값 매핑 수. 매핑·물리 셀 모두 미해결이 아니어야 한다."""
    cells = {c.cell_id: c for c in physical.cells}
    return sum(1 for m in table.cell_mappings if (m.row_path or m.column_path)
               and m.evidence_state != "unresolved" and cells[m.cell_id].status != "unresolved")


def _message(decision, **extra):
    return json.dumps(dict(version=REVIEW_VERSION, reason=decision.reason, source=decision.source,
                           choice=decision.choice, supports=list(decision.supports),
                           counters=list(decision.counters), evidence_ids=list(decision.evidence_ids),
                           semantic_status="review_candidate_not_human_validated", **extra),
                      ensure_ascii=False)


# 머리글 해석 자체를 막는 반대 근거. MERGED_HEADER_ROW·VALUE_CELLS_FILLED는 '머리글 1행·라벨 1열' 가정에서 나온
# 신호라, 라벨이 여러 열인 표(걸친 머리글·색칠된 하위 라벨)에서는 반대 근거가 아니다.
BLOCKING_COUNTERS = {"UNRESOLVED_PHYSICAL_CELL", "HEADER_LABEL_NOT_TEXTUAL", "DUPLICATE_HEADER_LABELS"}


def holds_table(cell, table, physical):
    """그 칸 안에 같은 쪽의 다른 표가 통째로 들어 있는가(좌표만 본다). 표를 담은 칸은 내용 칸이지 머리글이 아니다."""
    box = cell.source_bbox
    for other in physical.values():
        if other.table_id == table.table_id or other.page_number != table.page_number:
            continue
        b = other.bounding_box
        if box.x0 - 1 <= b.x0 and box.y0 - 1 <= b.y0 and b.x1 <= box.x1 + 1 and b.y1 <= box.y1 + 1:
            return True
    return False


def header_holds_table(table, header_rows, physical):
    return any(c.row_index is not None and c.row_index < header_rows and holds_table(c, table, physical)
               for c in table.cells)


def row_depth_decisions(physical, structured, decisions, pages):
    """라벨 경계에 걸친 칸이 있는 표에 행별 라벨 깊이 배치를 붙인다(규칙, 점유 범위만 사용).

    머리글 판정이 결정된 표는 그 결정(머리글 행·라벨 열)에서, 나머지 한 패널 표는 현재 분석 결과에서 출발한다.
    분석기가 가정한 머리글 행을 그대로 쓰려면 머리글 근거(배경 띠·굵은 글씨·숫자 대비·길이 대비 중 하나)가 있어야 한다.
    근거가 없으면 첫 행이 데이터일 수 있으므로(머리글 없는 라벨|내용 표) 적용하지 않고 후보만 남겨 보류한다.
    """
    by_table = {d.table_id: d for d in decisions if d.aspect == "layout"}
    out = []
    for tid, st in structured.items():
        d = by_table.get(tid)
        if d is not None and d.status == "decided":
            header, stub = d.choice["header_rows"], d.choice["stub_columns"]
        elif d is not None:
            continue  # 머리글 판정이 보류·해당 없음인 표: 행별 깊이로 머리글 해석을 대신 확정하지 않는다.
        elif len(st.panels) == 1:
            header, stub = st.panels[0].header_rows, st.panels[0].stub_columns
        else:
            continue
        layout = derive_layout(physical[tid], header, stub)
        if layout is None:
            continue
        choice = dict(header_rows=layout.header_rows, stub_columns=max(layout.body_depths()),
                      row_stub_depths=list(layout.row_stub_depths))
        evidence = tuple(c.cell_id for c in physical[tid].cells)
        if not (d is not None and d.status == "decided"):
            graded = grade(physical[tid], pages[physical[tid].page_number])
            blocked = BLOCKING_COUNTERS & set(graded["counters"])
            if not graded["supports"] or blocked:
                # 머리글 근거가 없다. 머리글 없는 배치로 보면 첫 행까지 모두 '짧은 라벨 | 긴 내용'인 표는 규칙으로 정한다.
                headerless = derive_layout(physical[tid], 0, stub) or TableLayout.uniform(len(physical[tid].rows), 0, stub)
                without = dict(header_rows=0, stub_columns=max(headerless.body_depths()),
                               row_stub_depths=list(headerless.row_stub_depths))
                # 첫 행 칸이 표를 담고 있으면 그 행은 머리글일 수 없다(내용 칸). 물리 셀이 확정일 때만 규칙으로 정한다.
                first_row_content = (header_holds_table(physical[tid], 1, physical)
                                     and "UNRESOLVED_PHYSICAL_CELL" not in graded["counters"])
                if first_row_content or (not blocked and key_value_rows(physical[tid], headerless)):
                    out.append(Decision(
                        table_id=tid, aspect="layout", status="decided",
                        reason="FIRST_ROW_HOLDS_TABLE" if first_row_content else "KEY_VALUE_ROWS", choice=without,
                        supports=("FIRST_ROW_HOLDS_TABLE" if first_row_content else "KEY_VALUE_SHAPE",
                                  "ROW_DEPTH_FROM_SPANS"),
                        evidence_ids=evidence, candidates=(choice, without)))
                    continue
                out.append(Decision(table_id=tid, aspect="layout", status="ambiguous",
                                    reason="HEADER_CONFLICT" if graded["supports"] else "HEADER_NOT_SUPPORTED",
                                    counters=tuple(graded["counters"]), evidence_ids=evidence,
                                    candidates=(choice, without)))
                continue
        if d is not None and d.status == "decided":
            out.append(d.model_copy(update=dict(choice=choice, supports=d.supports + ("ROW_DEPTH_FROM_SPANS",))))
        else:
            out.append(Decision(table_id=tid, aspect="layout", status="decided", reason="ROW_DEPTH_FROM_SPANS",
                                choice=choice, supports=("ROW_DEPTH_FROM_SPANS",) + tuple(graded["supports"]),
                                evidence_ids=evidence))
    replaced = {d.table_id for d in out if d.status == "decided"}
    return [d for d in decisions if not (d.aspect == "layout" and d.status == "decided" and d.table_id in replaced)] + out


def key_value_rows(table, layout):
    """머리글 없는 배치로 봤을 때 첫 행을 포함한 모든 행이 '짧은 라벨 | 긴 내용'인가(같은 표 안의 길이 비교, 언어 무관).

    첫 행의 내용이 짧으면(머리글일 수 있음) 판단하지 않는다.
    """
    from doc_understanding.parser.structure.trees import ranges
    rows = {}
    for cell in table.cells:
        span = ranges(cell)
        if not span:
            continue
        (r0, r1), (c0, c1) = span
        text = "".join((cell.original_text or "").split())
        depths = layout.depths((r0, r1))
        if not depths:
            return False
        kind = "label" if c1 <= min(depths) else "value" if c0 >= max(depths) else None
        if kind is None:
            return False
        for r in range(r0, r1):
            entry = rows.setdefault(r, dict(label=0, value=0))
            entry[kind] = max(entry[kind], len(text)) if kind == "label" else entry[kind] + len(text)
    if len(rows) < 2 or len(rows) != len(table.rows):
        return False
    long = lambda row: row["value"] >= max(12, 2 * row["label"])  # noqa: E731
    return (long(rows[0]) and median(r["value"] for r in rows.values())
            >= max(12, 2 * median(r["label"] for r in rows.values())))


def layout_candidates(table, stub_columns):
    """규칙이 만드는 후보 배치: 첫 행이 머리글인 경우와 머리글이 없는 경우. 둘 다 행별 라벨 깊이를 점유에서 정한다."""
    out = []
    for header in (1, 0):
        layout = derive_layout(table, header, stub_columns) or TableLayout.uniform(len(table.rows), header, stub_columns)
        out.append(dict(header_rows=header, stub_columns=max(layout.body_depths()),
                        row_stub_depths=list(layout.row_stub_depths)))
    return tuple(out)


def analyze_choice(pt, choice, page):
    if "row_stub_depths" in choice or choice["header_rows"] == 0:
        depths = tuple(choice.get("row_stub_depths") or (choice["stub_columns"],) * len(pt.rows))
        layout = TableLayout(choice["header_rows"], depths)
        return SemanticTableAnalyzer().analyze(pt, layout=layout, source_page=page)
    return SemanticTableAnalyzer().analyze(pt, header_rows=choice["header_rows"],
                                           stub_columns=choice["stub_columns"], source_page=page)


def _rebuild(doc, tables):
    tables = tuple(tables)
    candidate = doc.model_copy(update=dict(
        table_structure=tables,
        column_hierarchy=tuple(n for t in tables for n in t.column_tree),
        row_hierarchy=tuple(n for t in tables for n in t.row_tree),
        cell_mappings=tuple(m for t in tables for m in t.cell_mappings)))
    validate_document(candidate)
    return candidate


def review_document(doc, *, policy=None, allow_external=False, output=None, ledger=None, transport=None):
    """검토 후보 문서와 보고서를 반환한다. 적용할 변경이 없으면 문서는 None이다."""
    pages = {p.page_number: p for p in doc.document_ir.pages}
    physical = {t.table_id: t for t in doc.physical_tables}
    structured = {t.table_id: t for t in doc.table_structure}
    decisions = [decide_layout(physical[tid], pages[physical[tid].page_number])
                 for tid, st in structured.items()
                 if any(a.code == "HEADER_REGION_AMBIGUOUS" for a in st.ambiguities)]
    # 규칙을 먼저 끝까지 적용한다(머리글 근거 → 행별 라벨 깊이 → 머리글 없는 라벨|내용 표).
    decisions = row_depth_decisions(physical, structured, decisions, pages)
    # 규칙이 모호로 남긴 배치 판정에는 규칙이 만든 후보 배치를 붙인다. 모델은 이 후보 중에서만 고른다.
    decisions = [d.model_copy(update=dict(candidates=layout_candidates(physical[d.table_id], 1)))
                 if d.aspect == "layout" and d.status == "ambiguous"
                 and not any("row_stub_depths" in c for c in d.candidates) else d for d in decisions]
    decisions += [d for d in decide_scopes(doc) if d.table_id in structured]
    if policy is not None and output is not None:
        # 모델은 규칙이 모호(ambiguous)로 남긴 판정에만 쓰인다(resolve는 그 밖의 판정을 그대로 돌려준다).
        decisions = [resolve(d, layout_context(physical[d.table_id]) if d.aspect == "layout" else {},
                             policy, ledger, output, allow_external=allow_external, transport=transport)
                     for d in decisions]

    revised, applied, rejected = dict(structured), [], []
    for d in decisions:
        if d.aspect != "layout" or d.status != "decided":
            continue
        pt = physical[d.table_id]
        if d.choice["header_rows"] and header_holds_table(pt, d.choice["header_rows"], physical):
            # 규칙·모델 어느 쪽 선택이든, 표를 담은 칸을 머리글로 삼는 배치는 받지 않는다.
            rejected.append(dict(table_id=d.table_id, aspect="layout", reason="HEADER_CELL_HOLDS_TABLE"))
            continue
        rebuilt = numbered_view(analyze_choice(pt, d.choice, pages[pt.page_number]), pt)
        if any(i.code == "REPEATED_STUB_AS_VALUE" for i in rebuilt.issues):
            # 반복 머리글 패널의 라벨 열을 값으로 읽은 배치는 받지 않는다(오른쪽 패널 값이 왼쪽 라벨에 붙는다).
            rejected.append(dict(table_id=d.table_id, aspect="layout", reason="REPEATED_PANELS_FLATTENED"))
            continue
        before, after = usable(structured[d.table_id], pt), usable(rebuilt, pt)
        if after <= before or rebuilt.analysis_state == "invalid":
            rejected.append(dict(table_id=d.table_id, aspect="layout", reason="REBUILD_NOT_IMPROVED",
                                 usable_before=before, usable_after=after))
            continue
        issue = Issue(code=LAYOUT_CODE, severity="info", source_references=(pt.source_references[0],),
                      message=_message(d, usable_before=before, usable_after=after))
        trial = dict(revised, **{d.table_id: rebuilt.model_copy(update={"issues": rebuilt.issues + (issue,)})})
        try:
            # 문맥 연결이 사라진 노드를 가리키는 등 문서 검증에 실패하면 이 표의 변경만 버린다.
            _rebuild(doc, (trial[t.table_id] for t in doc.physical_tables))
        except ValueError as error:
            rejected.append(dict(table_id=d.table_id, aspect="layout", reason="DOCUMENT_VALIDATION_FAILED:" + str(error)))
            continue
        revised = trial
        applied.append(dict(table_id=d.table_id, aspect="layout", source=d.source,
                            usable_before=before, usable_after=after))
    contents = {c.content_id: c for c in doc.contents}
    for d in decisions:
        if d.aspect != "scope" or d.status != "decided":
            continue
        heading = contents.get(d.choice["content_id"])
        if heading is None:
            rejected.append(dict(table_id=d.table_id, aspect="scope", reason="HEADING_CONTENT_MISSING"))
            continue
        issue = Issue(code=SCOPE_CODE, severity="info", source_references=heading.source_references,
                      message=_message(d, content_id=heading.content_id, text_raw=heading.text_raw))
        table = revised[d.table_id]
        revised[d.table_id] = table.model_copy(update={"issues": table.issues + (issue,)})
        applied.append(dict(table_id=d.table_id, aspect="scope", source=d.source, text_raw=heading.text_raw))

    report = dict(
        version=REVIEW_VERSION, document_id=doc.document_id,
        decisions=[d.model_dump(mode="json") for d in decisions], applied=applied, rejected=rejected,
        summary=dict(
            layout_decided=sum(d.aspect == "layout" and d.status == "decided" for d in decisions),
            layout_ambiguous=sum(d.aspect == "layout" and d.status == "ambiguous" for d in decisions),
            scope_decided=sum(d.aspect == "scope" and d.status == "decided" for d in decisions),
            scope_ambiguous=sum(d.aspect == "scope" and d.status == "ambiguous" for d in decisions),
            usable_values_before=sum(usable(structured[t], physical[t]) for t in structured),
            usable_values_after=sum(usable(revised[t], physical[t]) for t in structured)),
        model_calls=list(ledger.calls) if ledger else [],
        estimated_model_cost_usd=ledger.estimated_usd if ledger else 0.0,
        semantic_status="review_candidate_not_human_validated")
    if not applied:
        return None, report
    # Rebuilding a layout allocates a transient analyzer UUID. Identify the stored review
    # by its source analysis and final candidate (including scope decisions and provenance).
    for tid in {item["table_id"] for item in applied if item["aspect"] == "layout"}:
        table = revised[tid]
        identity = uuid5(structured[tid].analysis_run_id, table.model_dump_json(exclude={"analysis_run_id"}))
        revised[tid] = table.model_copy(update={"analysis_run_id": identity})
    reviewed = _rebuild(doc, (revised[t.table_id] for t in doc.physical_tables))
    note = Issue(code="STRUCTURE_REVIEW_CANDIDATE", severity="warning",
                 message=f"{REVIEW_VERSION}: layout/scope review candidates applied; not human-validated.")
    reviewed = reviewed.model_copy(update={"issues": reviewed.issues + (note,)})
    validate_document(reviewed)
    if reviewed.document_ir != doc.document_ir or reviewed.physical_tables != doc.physical_tables:
        raise ValueError("REVIEW_CHANGED_SOURCE")
    return reviewed, report
