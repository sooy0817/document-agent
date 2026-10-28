"""Conservative hierarchy hypotheses from occupancy and repeated source labels."""

import hashlib
from collections import Counter
from uuid import uuid4
from .repeated_panels import repeated_panel_ranges

from doc_understanding.domain.models import Issue
from .semantic_models import (
    Ambiguity,
    CellPath,
    CellRole,
    HierarchyNode,
    Panel,
    StructuralFeature,
    StructuredTableIR,
    VisionStructureRequest,
)


def ranges(cell):
    if not cell.occupied_slots:
        return None
    rr, cc = zip(*cell.occupied_slots)
    return (min(rr), max(rr) + 1), (min(cc), max(cc) + 1)


def physical_state(cell, table):
    edges = {b.boundary_id: b for b in table.boundaries}
    states = [edges[i].status for i in cell.boundary_ids if i in edges]
    if cell.status == "unresolved" or "uncertain" in states:
        return "unresolved"
    if cell.status == "inferred" or "inferred" in states:
        return "inferred"
    return "observed"


def contains(outer, inner):
    return outer[0] <= inner[0] and inner[1] <= outer[1]


def parent_candidates(node, nodes):
    if node.axis == "row":
        candidates = [
            p
            for p in nodes
            if p.column_range[1] <= node.column_range[0] and contains(p.row_range, node.row_range)
        ]
        nearest = max((p.column_range[1] for p in candidates), default=-1)
        return [p for p in candidates if p.column_range[1] == nearest]
    candidates = [
        p
        for p in nodes
        if p.row_range[1] <= node.row_range[0] and contains(p.column_range, node.column_range)
    ]
    nearest = max((p.row_range[1] for p in candidates), default=-1)
    return [p for p in candidates if p.row_range[1] == nearest]


def build_tree(cells, axis, panel, table):
    nodes = []
    for c in sorted(cells, key=lambda c: (ranges(c)[0], ranges(c)[1], c.cell_id)):
        rr, cc = ranges(c)
        state = physical_state(c, table)
        nodes.append(
            HierarchyNode(
                node_id=f"{panel.panel_id}/{axis}/{c.cell_id}",
                axis=axis,
                panel_id=panel.panel_id,
                label_raw=c.original_text,
                source_cell_id=c.cell_id,
                bbox=c.source_bbox,
                row_range=rr,
                column_range=cc,
                physical_evidence=state,
                evidence_state="unresolved"
                if state == "unresolved" or not (c.original_text or "").strip()
                else "inferred",
                boundary_ids=c.boundary_ids,
            )
        )
    connected = []
    for n in nodes:
        parents = parent_candidates(n, nodes)
        connected.append(
            n.model_copy(
                update={
                    "parent_id": parents[0].node_id if len(parents) == 1 else None,
                    "candidate_parent_ids": tuple(p.node_id for p in parents)
                    if len(parents) > 1
                    else (),
                    "evidence_state": "unresolved" if len(parents) > 1 else n.evidence_state,
                }
            )
        )
    # Propagate uncertainty through the complete ancestor chain.
    index = {n.node_id: n for n in connected}
    for _ in connected:
        changed = False
        for key, n in list(index.items()):
            if (
                n.parent_id
                and index[n.parent_id].evidence_state == "unresolved"
                and n.evidence_state != "unresolved"
            ):
                index[key] = n.model_copy(update={"evidence_state": "unresolved"})
                changed = True
        if not changed:
            break
    return tuple(index.values())


def path_for(node, nodes):
    index = {n.node_id: n for n in nodes}
    path, seen = [], set()
    while node:
        if node.node_id in seen:
            return ()
        seen.add(node.node_id)
        path.append(node.node_id)
        node = index.get(node.parent_id)
    return tuple(reversed(path))


def leaves_for(cell, nodes, axis):
    span = ranges(cell)[0 if axis == "row" else 1]
    if axis == "column":
        parents = {n.parent_id for n in nodes}
        leaves = [n for n in nodes if n.node_id not in parents]
        containing = [n for n in leaves if contains(n.column_range, span)]
        return containing or [
            n for n in leaves if max(n.column_range[0], span[0]) < min(n.column_range[1], span[1])
        ]
    candidates = [
        n for n in nodes if contains(n.row_range if axis == "row" else n.column_range, span)
    ]
    # Only the deepest matching nodes are eligible; intersecting, non-containing
    # merged ranges never make a child.
    ancestors = {i for n in candidates for i in path_for(n, nodes)[:-1]}
    return [n for n in candidates if n.node_id not in ancestors]


class SemanticTableAnalyzer:
    def analyze(self, table, adjacent_tables=(), *, header_rows=None, stub_columns=None):
        ambiguities = []
        features = []

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

        def feature(kind, cells, rule, disposition="candidate"):
            features.append(
                StructuralFeature(
                    type=kind,
                    disposition=disposition,
                    evidence_cell_ids=tuple(c.cell_id for c in cells),
                    rule=rule,
                )
            )

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
        ambiguity(
            "LAYOUT_HYPOTHESIS",
            "Header/stub inferred from top-left occupancy and leading body merges; semantic roles need review.",
            (corner,) if corner else (),
            (f"header_rows={header};stub_columns={stub}",),
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
        repeated = repeated_panel_ranges(table) if header_rows is None and stub_columns is None else ()
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
        for panel in panels:
            stub_end = panel.column_range[0] + panel.stub_columns
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
                    if rr[1] <= header and cc[1] <= stub_end:
                        role = "corner"
                    elif rr[1] <= header and cc[0] >= stub_end:
                        role = "column_header"
                        colcells.append(c)
                    elif rr[0] >= header and cc[1] <= stub_end:
                        role = "row_label"
                        rowcells.append(c)
                    elif rr[0] >= header and cc[0] >= stub_end:
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
            if corner is None or (header == 1 and stub == 1):
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
            mappings = []
            for c in values:
                r = leaves_for(c, rows, "row")
                h = leaves_for(c, columns, "column")
                rp = path_for(r[0], rows) if len(r) == 1 and not parallel else ()
                cp = path_for(h[0], columns) if len(h) == 1 and not parallel else ()
                state = "inferred"
                if (
                    not rp
                    or not cp
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
            analyzer_version="semantic-geometry/0.2.0",
            features=tuple(features),
            row_tree=rows,
            column_tree=columns,
            cell_roles=tuple(roles),
            cell_mappings=tuple(mappings),
            ambiguities=tuple(ambiguities),
            issues=(),
            analysis_state="partial" if mappings else "review_required",
        )
        issues = validate_hierarchy(table, output)
        return output.model_copy(
            update={
                "issues": issues,
                "analysis_state": "invalid"
                if any(i.severity == "error" for i in issues)
                else output.analysis_state,
            }
        )


def validate_hierarchy(physical, structured):
    issues = []
    cells = {c.cell_id: c for c in physical.cells}

    def issue(code, message, error=True):
        issues.append(
            Issue(
                code=code,
                severity="error" if error else "warning",
                message=message,
                source_references=physical.source_references,
            )
        )

    if physical.cells and not structured.cell_mappings:
        issue("NO_BODY_MAPPING", "Source cells exist but no body mapping was produced; role coverage is not semantic completeness.", False)
    if physical.rows and any(p.header_rows >= len(physical.rows) for p in structured.panels):
        issue("HEADER_CONSUMES_ALL_ROWS", "Proposed header covers every row; no-body interpretation requires review.", False)
    panel_index = {p.panel_id:p for p in structured.panels}
    if len(panel_index) != len(structured.panels):
        issue("DUPLICATE_PANEL_ID", "Panel IDs must be unique.")
    for m in structured.cell_mappings:
        c = cells.get(m.cell_id); p = panel_index.get(m.panel_id)
        if not p or (c and ranges(c) and not contains(p.column_range, ranges(c)[1])):
            issue("PANEL_GEOMETRY_CONFLICT", m.cell_id)
    suggested = repeated_panel_ranges(physical)
    if suggested:
        role_map = {r.cell_id:r.role for r in structured.cell_roles}
        misplaced = [c.cell_id for c in physical.cells if c.row_index > 0
                     and c.column_index in {a for a,b in suggested}
                     and (c.original_text or '').strip() and role_map.get(c.cell_id) == 'value']
        if misplaced:
            issue("REPEATED_STUB_AS_VALUE", "Repeated-panel label candidates classified as values: " + ", ".join(misplaced), False)

    nodes = structured.row_tree + structured.column_tree
    if len(cells) != len(physical.cells):
        issue("DUPLICATE_SOURCE_CELL_ID", "Physical cell IDs are not unique.")
    for c in physical.cells:
        span = ranges(c)
        if span:
            rr, cc = span
            rectangle = {(r, k) for r in range(*rr) for k in range(*cc)}
            if (
                set(c.occupied_slots) != rectangle
                or rr[0] < 0
                or cc[0] < 0
                or rr[1] > len(physical.rows)
                or cc[1] > len(physical.columns)
            ):
                issue("INVALID_SOURCE_OCCUPANCY", c.cell_id)
    index = {n.node_id: n for n in nodes}
    if len(index) != len(nodes):
        issue("DUPLICATE_NODE_ID", "Node IDs must be unique.")
    for n in nodes:
        if n.source_cell_id not in cells:
            issue("SOURCE_REF_MISSING", n.node_id)
            continue
        c = cells[n.source_cell_id]
        if n.label_raw != c.original_text:
            issue("SOURCE_TEXT_CHANGED", n.node_id)
        if ranges(c) != (n.row_range, n.column_range) or c.source_bbox != n.bbox:
            issue("SOURCE_GEOMETRY_MISMATCH", n.node_id)
        expected = physical_state(c, physical)
        if n.physical_evidence != expected or n.evidence_state == "observed":
            issue("EVIDENCE_PROMOTION", n.node_id)
        if expected == "unresolved" and n.evidence_state != "unresolved":
            issue("UNCERTAINTY_LOST", n.node_id)
        if any(i not in index for i in n.candidate_parent_ids):
            issue("CANDIDATE_PARENT_MISSING", n.node_id)
        if n.parent_id:
            p = index.get(n.parent_id)
            if (
                not p
                or p.axis != n.axis
                or p.panel_id != n.panel_id
                or p not in parent_candidates(n, [p])
            ):
                issue("PARENT_OCCUPANCY_CONFLICT", n.node_id)
            elif p.evidence_state == "unresolved" and n.evidence_state != "unresolved":
                issue("UNCERTAINTY_LOST", n.node_id)
        if not path_for(n, nodes):
            issue("TREE_CYCLE", n.node_id)
    roles = [r.cell_id for r in structured.cell_roles]
    if set(roles) != set(cells) or len(roles) != len(cells):
        issue("CELL_ROLE_COVERAGE", "Every physical cell needs exactly one role.")
    mapped = [m.cell_id for m in structured.cell_mappings]
    expected_values = {r.cell_id for r in structured.cell_roles if r.role == "value"}
    if set(mapped) != expected_values or len(mapped) != len(set(mapped)):
        issue("MAPPING_COVERAGE", "Every value cell needs exactly one mapping record.")
    seen = set()
    for m in structured.cell_mappings:
        c = cells.get(m.cell_id)
        if c is None or c.source_bbox != m.bbox or c.original_text != m.original_text:
            issue("MAPPING_SOURCE_MISMATCH", m.cell_id)
        for axis, path in (("row", m.row_path), ("column", m.column_path)):
            if path and (
                any(i not in index for i in path) or path != path_for(index[path[-1]], nodes)
            ):
                issue("INVALID_PATH", m.cell_id)
                continue
            if path and any(index[i].axis != axis or index[i].panel_id != m.panel_id for i in path):
                issue("PANEL_CROSS_MAPPING", m.cell_id)
            if path and c and ranges(c):
                n = index[path[-1]]
                if not contains(
                    n.row_range if axis == "row" else n.column_range, ranges(c)[axis == "column"]
                ):
                    issue("PATH_OCCUPANCY_CONFLICT", m.cell_id)
            if (
                path
                and any(index[i].evidence_state == "unresolved" for i in path)
                and m.evidence_state != "unresolved"
            ):
                issue("UNCERTAINTY_LOST", m.cell_id)
        if m.evidence_state == "observed" or (
            c and physical_state(c, physical) == "unresolved" and m.evidence_state != "unresolved"
        ):
            issue("EVIDENCE_PROMOTION", m.cell_id)
        if not m.row_path or not m.column_path:
            issue("CELL_UNMAPPED", m.cell_id, False)
        pair = m.row_path, m.column_path
        if pair in seen and all(pair):
            issue("PATH_COLLISION", m.cell_id, m.evidence_state != "unresolved")
        seen.add(pair)
    if (
        structured.physical_boundaries != physical.boundaries
        or structured.physical_unresolved_boundaries != physical.unresolved_boundaries
    ):
        issue("BOUNDARY_EVIDENCE_CHANGED", "Physical boundaries must remain unchanged.")
    return tuple(issues)


def vision_request(physical, structured):
    return VisionStructureRequest(
        physical=physical,
        structured=structured,
        ambiguities=structured.ambiguities,
        allowed_cell_ids=tuple(c.cell_id for c in physical.cells),
        allowed_node_ids=tuple(n.node_id for n in structured.row_tree + structured.column_tree),
    )


def validate_proposal(request, proposal):
    """Membership gate only, never applies or approves a proposed patch."""
    allowed = set(request.allowed_node_ids) | set(request.allowed_cell_ids)
    errors = []
    if proposal.target_id not in allowed:
        errors.append("UNKNOWN_TARGET")
    if proposal.parent_id and proposal.parent_id not in request.allowed_node_ids:
        errors.append("UNKNOWN_PARENT")
    if not proposal.evidence_cell_ids or not set(proposal.evidence_cell_ids) <= set(
        request.allowed_cell_ids
    ):
        errors.append("UNKNOWN_OR_MISSING_EVIDENCE")
    if proposal.operation == "propose_parent" and proposal.parent_id == proposal.target_id:
        errors.append("SELF_PARENT")
    return tuple(errors)
