from types import SimpleNamespace as NS

from doc_understanding.domain.models import BBox, SourceRef
from doc_understanding.parser.physical.physical_models import LineSegment
from doc_understanding.parser.physical.diagram_gate import classify_page, analyze_allowed


def fixture(rects, connectors=()):
    cells, lines = [], []
    for i, xy in enumerate(rects):
        b = BBox(x0=xy[0], y0=xy[1], x1=xy[2], y1=xy[3])
        cells.append(NS(cell_id=f"c{i}", source_bbox=b, span_refs=(f"s{i}",)))
        for axis, pos, start, end in (
            ("horizontal", b.y0, b.x0, b.x1),
            ("horizontal", b.y1, b.x0, b.x1),
            ("vertical", b.x0, b.y0, b.y1),
            ("vertical", b.x1, b.y0, b.y1),
        ):
            lines.append((axis, pos, start, end))
    lines += list(connectors)
    bbox = BBox(x0=0, y0=0, x1=300, y1=300)
    source = SourceRef(document_id="test", page_number=1, bbox=bbox, block_id="drawing")
    observed = tuple(
        LineSegment(
            line_id=f"l{i}", axis=a, position=p, start=s, end=e, source=source, method="vector"
        )
        for i, (a, p, s, e) in enumerate(lines)
    )
    table = NS(table_id="table", bounding_box=bbox, cells=cells, lines=observed)
    return NS(page_number=1, blocks=()), [table]


def test_grid_with_merged_header_continues_without_semantic_approval():
    page, tables = fixture([(0, 0, 100, 20), (0, 20, 50, 60), (50, 20, 100, 60)])
    gate = classify_page(page, tables)
    assert gate.decisions[0].action == "continue_semantic_candidate"
    assert gate.decisions[0].semantic_status == "not_validated"


def test_multiple_connectors_between_boxes_skip_semantic_call():
    page, tables = fixture(
        [(0, 0, 40, 40), (60, 0, 100, 40), (120, 0, 160, 40)],
        [("horizontal", 20, 40, 60), ("horizontal", 20, 100, 120)],
    )
    gate = classify_page(page, tables)
    assert gate.decisions[0].classification == "diagram_candidate"

    def forbidden(_):
        raise AssertionError("diagram reached semantic analyzer")

    assert analyze_allowed(tables, gate, NS(analyze=forbidden)) == ()
    assert len(tables[0].cells) == 3
    assert gate.decisions[0].source_span_ids == ("s0", "s1", "s2")


def test_disconnected_boxes_without_connectors_are_unknown_not_diagram():
    page, tables = fixture([(0, 0, 40, 40), (60, 0, 100, 40), (120, 0, 160, 40)])
    assert classify_page(page, tables).decisions[0].classification == "mixed_or_unknown"


def test_single_connector_is_not_enough_and_mixed_region_is_held():
    page, tables = fixture([(0, 0, 40, 40), (60, 0, 100, 40)], [("horizontal", 20, 40, 60)])
    assert classify_page(page, tables).decisions[0].action == "hold_table_semantics"
    page, tables = fixture(
        [
            (0, 0, 40, 40),
            (60, 0, 100, 40),
            (120, 0, 160, 40),
            (0, 100, 40, 140),
            (40, 100, 80, 140),
        ],
        [("horizontal", 20, 40, 60), ("horizontal", 20, 100, 120)],
    )
    assert classify_page(page, tables).decisions[0].action == "hold_table_semantics"


def test_diagram_does_not_skip_another_table_on_same_page():
    page, diagram = fixture(
        [(0, 0, 40, 40), (60, 0, 100, 40), (120, 0, 160, 40)],
        [("horizontal", 20, 40, 60), ("horizontal", 20, 100, 120)],
    )
    _, normal = fixture([(0, 200, 40, 240), (40, 200, 80, 240)])
    normal[0].table_id = "normal"
    # Distinct source line IDs, as required by the actual IR contract.
    normal[0].lines = tuple(
        x.model_copy(update={"line_id": "normal-" + x.line_id}) for x in normal[0].lines
    )
    tables = diagram + normal
    gate = classify_page(page, tables)
    assert gate.decisions[1].action == "continue_semantic_candidate"
    assert analyze_allowed(tables, gate, NS(analyze=lambda t: t.table_id)) == ("normal",)
