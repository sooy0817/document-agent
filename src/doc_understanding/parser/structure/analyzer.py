# 역할: 물리 표에서 역할·행열 트리·값 경로 후보를 생성하고 검증한다. 원본 셀은 수정하지 않는다.
"""Conservative hierarchy hypotheses from occupancy and repeated source labels."""

import hashlib
from collections import Counter
from uuid import uuid4
from doc_understanding.parser.structure.repeated_panels import repeated_panel_ranges
from doc_understanding.parser.structure.header_evidence import source_header_evidence, type_contrast_header_evidence

from doc_understanding.parser.structure.layout import TableLayout
from doc_understanding.parser.structure.models import Ambiguity, CellPath, CellRole, Panel, StructuralFeature, StructuredTableIR

# 기존 호출부가 사용하는 함수 이름은 유지하되 구현은 역할별 파일에 둔다.
from doc_understanding.parser.structure.trees import ranges as ranges, physical_state as physical_state, contains as contains, parent_candidates as parent_candidates, build_tree as build_tree, path_for as path_for, leaves_for as leaves_for
from doc_understanding.parser.structure.validation import validate_hierarchy as validate_hierarchy, vision_request as vision_request, validate_proposal as validate_proposal


# PhysicalTableIR을 읽어 역할·행열 경로 후보를 생성하는 규칙 기반 분석기다. 모델 호출은 없다.
class SemanticTableAnalyzer:
    # 헤더·행 라벨 영역 → 패널 → 트리 → 값 셀 경로 → 검증 순서로 StructuredTableIR을 반환한다.
    # 향후 경로 고도화 시 원본 셀·좌표를 고치지 않고 역할 경계, 부모 후보, 말단 선택을 함께 확인한다.
    # layout(TableLayout)을 주면 그 배치(머리글 행 수·행별 라벨 깊이)로 역할을 정한다. 주지 않으면 예전처럼
    # 점유·병합에서 '머리글 n행·라벨 m열' 하나를 추정한다(결과는 예전과 같다).
    def analyze(self, table, adjacent_tables=(), *, header_rows=None, stub_columns=None, source_page=None,
                layout=None):
        ambiguities = []
        features = []
        source_proofs = []

        # 확정하지 못한 해석과 대상 셀·경쟁 후보·검토 위치를 같은 분석 결과에 기록한다.
        def ambiguity(code, reason, cells=(), candidates=()):
            ambiguities.append(
                Ambiguity(
                    ambiguity_id=f"{table.table_id}/a{len(ambiguities)}",
                    code=code,
                    reason=reason,
                    target_ids=tuple(c.cell_id for c in cells),
                    candidate_ids=tuple(candidates),
                    review_regions=tuple(c.source for c in cells) or table.source_references,
                )
            )

        # 구조 특성이 관찰되거나 추정된 이유와 근거 셀을 기록한다. 특성 검출은 의미 승인이 아니다.
        def feature(kind, cells, rule, disposition="candidate"):
            features.append(
                StructuralFeature(
                    type=kind,
                    disposition=disposition,
                    evidence_cell_ids=tuple(c.cell_id for c in cells),
                    rule=rule,
                )
            )

        # 1. 원본 점유와 병합 범위로 헤더·행 라벨 경계 후보를 정한다.
        usable = [c for c in table.cells if ranges(c)]
        corners = [c for c in usable if ranges(c)[0][0] == 0 and ranges(c)[1][0] == 0]
        corner = corners[0] if len(corners) == 1 else None
        header = ranges(corner)[0][1] if corner else 1
        stub = ranges(corner)[1][1] if corner else 1
        # Body leading merges expose another label depth even with flat headers.
        leading = [
            c
            for c in usable
            if ranges(c)[0][0] >= header
            and ranges(c)[1][0] == 0
            and ranges(c)[0][1] - ranges(c)[0][0] > 1
        ]
        if stub == 1 and leading:
            stub = max(ranges(c)[1][1] for c in leading) + 1
        stub = min(stub, max(1, len(table.columns) - 1))
        # An adapter may supply explicit source-markup layout (HTML th rows).
        # These remain candidate roles; the original cells/geometry are unchanged.
        if header_rows is not None or stub_columns is not None:
            if (header_rows is None or stub_columns is None
                    or not 1 <= header_rows < len(table.rows)
                    or not 1 <= stub_columns < len(table.columns)):
                raise ValueError('INVALID_SOURCE_LAYOUT_HINT')
            header, stub = header_rows, stub_columns
        explicit = layout is not None
        if explicit:
            if header_rows is not None or stub_columns is not None:
                raise ValueError('INVALID_SOURCE_LAYOUT_HINT')
            body = layout.body_depths()
            if (len(layout.row_stub_depths) != len(table.rows) or not 0 <= layout.header_rows < len(table.rows)
                    or not body or any(not 0 <= d < len(table.columns) for d in body)):
                raise ValueError('INVALID_TABLE_LAYOUT')
            header, stub = layout.header_rows, max(body)
        else:
            # 예전 추정: 모든 행이 같은 라벨 깊이. 경계에 걸친 머리글 칸은 역할 미정으로 둔다.
            layout = TableLayout.uniform(len(table.rows), header, stub, header_spans_stub=False)
        stub_low = min(layout.body_depths(), default=stub)
        ambiguity(
            "LAYOUT_HYPOTHESIS",
            "Layout supplied as a candidate (header rows and per-row label depth); semantic roles need review."
            if explicit else
            "Header/stub inferred from top-left occupancy and leading body merges; semantic roles need review.",
            (corner,) if corner else (),
            (f"header_rows={header};stub_columns={stub}",)
            + ((f"row_stub_depths={','.join(map(str, layout.row_stub_depths))}",) if explicit else ()),
        )
        # Multiple matching corner blocks can indicate independent horizontal panels.
        # Never force a split from repetition alone: block mappings for this ambiguity.
        panel_corners = [
            c
            for c in usable
            if ranges(c)[0] == (0, header)
            and ranges(c)[1][1] - ranges(c)[1][0] == stub
            and ranges(c)[1][0] > stub
            and header > 1
        ]
        parallel = bool(
            panel_corners
            and any(
                ranges(c)[0][0] >= header
                and ranges(c)[1][0] == ranges(pc)[1][0]
                and ranges(c)[0][1] - ranges(c)[0][0] > 1
                for pc in panel_corners
                for c in usable
            )
        )
        if parallel:
            feature("side_by_side_panels", panel_corners, "repeated_corner_and_body_stub")
            ambiguity(
                "PANEL_SPLIT_AMBIGUOUS",
                "Repeated corner/stub layout may be independent panels; cross-panel mapping withheld.",
                panel_corners,
            )
        # 받은 배치가 '머리글 1행, 모든 본문 행의 라벨 1열'이면 반복 머리글 패널의 전제(패널마다 머리글 1행·라벨 1열)와
        # 같으므로 패널 검사를 그대로 한다. 검사하지 않으면 오른쪽 패널의 라벨 열이 값이 되어 왼쪽 라벨에 붙는다.
        # 다른 배치(머리글 없음, 행마다 다른 라벨 깊이)는 패널 전제와 달라 예전처럼 검사하지 않는다.
        flat = layout.header_rows == 1 and set(layout.body_depths()) == {1}
        automatic = not explicit and header_rows is None and stub_columns is None
        repeated = repeated_panel_ranges(table) if automatic or flat else ()
        if repeated:
            feature("side_by_side_panels", [c for c in usable if c.row_index == 0],
                    "flat_repeated_headers_disjoint_text_stubs_complete_body")
        panels = tuple(Panel(panel_id=f"{table.table_id}/panel-{a}-{b}",
                             column_range=(a,b), header_rows=1, stub_columns=1)
                       for a,b in repeated) if repeated else (
            Panel(panel_id=f"{table.table_id}/main", column_range=(0,len(table.columns)),
                  header_rows=header, stub_columns=stub),)
        all_rows, all_columns, all_roles, all_mappings = [], [], [], []
        all_rowcells, all_colcells, all_values = [], [], []
        # 2. 패널별 역할을 정하고 독립적인 행·열 트리를 구성한다.
        for panel in panels:
            offset = panel.column_range[0]
            # 배치를 받지 않았거나 여러 패널(반복 머리글)이면 예전 규칙 그대로 모든 행이 같은 라벨 깊이다
            # (표 범위를 벗어난 점유 같은 잘못된 물리 셀도 예전처럼 역할을 받고 검증 단계가 진단한다).
            uniform = panel.stub_columns if len(panels) > 1 or not explicit else None
            roles, rowcells, colcells, values = [], [], [], []
            for c in table.cells:
                span = ranges(c)
                if span and len(panels) > 1 and not contains(panel.column_range, span[1]):
                    continue
                if not span and panel != panels[0]:
                    continue
                role = "unknown"
                if span:
                    rr, cc = span
                    if rr[1] <= header:
                        # 머리글 행: 모든 본문 행에서 라벨인 열 안에 있으면 모서리, 그 밖이면 열 머리글.
                        low = offset + (uniform if uniform is not None else stub_low)
                        if cc[1] <= low:
                            role = "corner"
                        elif cc[0] >= low or layout.header_spans_stub:
                            role = "column_header"
                            colcells.append(c)
                    elif rr[0] >= header:
                        # 본문 행: 그 칸이 덮는 행들의 라벨 깊이로 라벨·값을 가른다.
                        depths = (uniform,) if uniform is not None else layout.depths(rr)
                        if not depths:
                            pass  # 표의 행 범위를 벗어난 점유: 역할 미정으로 두고 검증이 진단한다.
                        elif cc[1] <= offset + min(depths):
                            role = "row_label"
                            rowcells.append(c)
                        elif cc[0] >= offset + max(depths):
                            role = "value"
                            values.append(c)
                roles.append(
                    CellRole(
                        cell_id=c.cell_id,
                        role=role,
                        evidence_state="unresolved"
                        if role == "unknown" or physical_state(c, table) == "unresolved"
                        else "inferred",
                    )
                )
                if role == "unknown":
                    ambiguity(
                        "CELL_ROLE_UNRESOLVED",
                        "Cell has no occupancy or crosses the proposed header/stub boundary.",
                        (c,),
                    )
            rows = build_tree(rowcells, "row", panel, table)
            columns = build_tree(colcells, "column", panel, table)
            proof = source_header_evidence(table, source_page, panel) if corner is not None and header == 1 and stub == 1 else None
            if proof is None and corner is not None and header == 1 and stub == 1:
                proof = type_contrast_header_evidence(table, source_page, panel)
            if proof:
                source_proofs.append(proof)
            if header_rows is None and not explicit and (corner is None or (header == 1 and stub == 1)) and proof is None:
                ambiguity(
                    "HEADER_REGION_AMBIGUOUS",
                    "No merged corner/stub evidence distinguishes a header from the first data row; retain competing layouts.",
                    (corner,) if corner else (),
                    ("header_rows=0", "header_rows=1"),
                )
                rows = tuple(n.model_copy(update={"evidence_state": "unresolved"}) for n in rows)
                columns = tuple(n.model_copy(update={"evidence_state": "unresolved"}) for n in columns)
            for n in rows + columns:
                if not (n.label_raw or "").strip():
                    ambiguity(
                        "LABEL_UNAVAILABLE",
                        "Blank or unavailable source text cannot establish a semantic label.",
                        tuple(c for c in usable if c.cell_id == n.source_cell_id),
                    )
                if n.candidate_parent_ids:
                    ambiguity(
                        "PARENT_AMBIGUOUS",
                        "Multiple equally near containing parents.",
                        tuple(c for c in usable if c.cell_id == n.source_cell_id),
                        n.candidate_parent_ids,
                    )
            # 3. 유일한 행·열 말단이 있을 때만 값 셀에 경로를 연결한다.
            mappings = []
            for c in values:
                r = leaves_for(c, rows, "row")
                h = leaves_for(c, columns, "column")
                rp = path_for(r[0], rows) if len(r) == 1 and not parallel else ()
                cp = path_for(h[0], columns) if len(h) == 1 and not parallel else ()
                state = "inferred"
                # 배치가 그 축이 없다고 한 경우(머리글 없는 표의 열, 라벨 깊이 0인 행)는 빈 경로가 정상이다.
                # 그 밖의 빈 경로는 예전처럼 미확정이다. 두 경로가 모두 비면 항상 미확정이다.
                no_columns = explicit and header == 0
                row_depths = layout.depths(ranges(c)[0])
                no_rows = explicit and bool(row_depths) and not any(row_depths)
                if (
                    (not rp and not no_rows)
                    or (not cp and not no_columns)
                    or not (rp or cp)
                    or physical_state(c, table) == "unresolved"
                    or any(n.evidence_state == "unresolved" for n in r + h)
                ):
                    state = "unresolved"
                    ambiguity(
                        "CELL_PATH_UNRESOLVED",
                        "Missing/nonunique path or unresolved physical evidence; candidates retained.",
                        (c,),
                        [n.node_id for n in r + h],
                    )
                mappings.append(
                    CellPath(
                        cell_id=c.cell_id,
                        bbox=c.source_bbox,
                        original_text=c.original_text,
                        panel_id=panel.panel_id,
                        row_path=rp,
                        column_path=cp,
                        evidence_state=state,
                        row_candidates=tuple(n.node_id for n in r),
                        column_candidates=tuple(n.node_id for n in h),
                    )
                )
            all_rows.extend(rows); all_columns.extend(columns); all_roles.extend(roles)
            all_mappings.extend(mappings)
            all_rowcells.extend(rowcells); all_colcells.extend(colcells); all_values.extend(values)
        rows, columns = tuple(all_rows), tuple(all_columns)
        roles, mappings = all_roles, all_mappings
        rowcells, colcells, values = all_rowcells, all_colcells, all_values
        # 4. 같은 경로에 여러 셀이 연결되면 충돌로 남기고 정답으로 확정하지 않는다.
        pairs = Counter(
            (m.row_path, m.column_path) for m in mappings if m.row_path and m.column_path
        )
        for i, m in enumerate(mappings):
            if m.row_path and m.column_path and pairs[m.row_path, m.column_path] > 1:
                mappings[i] = m.model_copy(update={"evidence_state": "unresolved"})
                ambiguity(
                    "PATH_COLLISION",
                    "Multiple physical cells share a path; a missing row/column distinction needs review.",
                    tuple(c for c in values if c.cell_id == m.cell_id),
                )
        merged = [c for c in usable if (c.rowspan or 1) > 1 or (c.colspan or 1) > 1]
        if merged:
            feature(
                "merged_cells",
                merged,
                "physical_span",
                "observed"
                if all(physical_state(c, table) == "observed" for c in merged)
                else "candidate",
            )
        if any(n.parent_id for n in rows):
            feature("hierarchical_rows", rowcells, "horizontal_containment")
        if any(n.parent_id for n in columns):
            feature("multi_level_columns", colcells, "vertical_containment")
        if table.unresolved_boundaries:
            feature("irregular_grid", usable, "physical_boundary_review")
        for other in adjacent_tables:
            if (
                other.document_id != table.document_id
                or abs(other.page_number - table.page_number) != 1
            ):
                continue

            # 인접 표의 열 시작점을 표 너비로 정규화한다. 열 정렬만으로 표 연결을 확정하지 않는다.
            def signature(t):
                width = t.bounding_box.x1 - t.bounding_box.x0
                return (
                    [(b.start - t.bounding_box.x0) / width for b in t.columns] if width > 0 else []
                )

            a, b = signature(table), signature(other)
            if a and len(a) == len(b) and max(abs(x - y) for x, y in zip(a, b)) < 0.02:
                feature("continued_across_pages", usable, "adjacent_normalized_column_alignment")
                ambiguity(
                    "CONTINUATION_AMBIGUOUS",
                    "Adjacent column alignment is insufficient to join tables; keep independent.",
                    (),
                    (other.table_id,),
                )
        output = StructuredTableIR(
            table_id=table.table_id,
            document_id=table.document_id,
            page_number=table.page_number,
            analysis_run_id=uuid4(),
            physical_ir_sha256=hashlib.sha256(table.model_dump_json().encode()).hexdigest(),
            physical_boundaries=table.boundaries,
            physical_unresolved_boundaries=table.unresolved_boundaries,
            panels=panels,
            analyzer_version="semantic-geometry/0.3.1",
            features=tuple(features),
            row_tree=rows,
            column_tree=columns,
            cell_roles=tuple(roles),
            cell_mappings=tuple(mappings),
            ambiguities=tuple(ambiguities),
            issues=(),
            analysis_state="partial" if mappings else "review_required",
        )
        # 5. 원본 보존·구조 일관성을 검사한다. 의미 정답 승인은 별도다.
        issues = tuple(source_proofs) + validate_hierarchy(table, output)
        return output.model_copy(
            update={
                "issues": issues,
                "analysis_state": "invalid"
                if any(i.severity == "error" for i in issues)
                else output.analysis_state,
            }
        )
