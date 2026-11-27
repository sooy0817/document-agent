import json
from pathlib import Path

from doc_understanding.parser.physical.geometry import bbox
from doc_understanding.parser.physical.intersection_analyzer import GridIntersectionAnalyzer
from doc_understanding.parser.physical.physical_models import GridPolicy, LineSegment
from doc_understanding.parser.physical.region_detector import TableRegion
from doc_understanding.domain.models import SourceRef
from doc_understanding.domain.models import TextSpanIR


def line(axis, pos, start, end, index):
    return LineSegment(
        line_id=f"segment{index}",
        axis=axis,
        position=pos,
        start=start,
        end=end,
        source=SourceRef(document_id="test", page_number=1, bbox=bbox(0, 0, 100, 100)),
        method="vector",
    )


def test_small_internal_gap_preserves_raw_coverage():
    lines = (
        line("horizontal", 0, 0, 100, 0),
        line("horizontal", 50, 0, 49.4, 1),
        line("horizontal", 50, 50.6, 100, 2),
        line("horizontal", 100, 0, 100, 3),
        line("vertical", 0, 0, 100, 4),
        line("vertical", 100, 0, 100, 5),
    )
    grid = GridIntersectionAnalyzer(GridPolicy()).analyze(TableRegion(bbox(0, 0, 100, 100), lines))
    edge = next(b for b in grid.boundaries if b.axis == "horizontal" and b.row == 1)
    assert edge.status == "present"
    assert 0.98 < edge.coverage < 1
    assert edge.evidence_level == "small_gap_bridged"


def test_manual_annotation_partition_and_named_relations():
    gold = json.loads(Path("tests/golden/ganggu-physical-v1.json").read_text(encoding="utf-8"))
    assert gold["provenance"]["prediction_imports"] is False
    for t in gold["tables"]:
        slots = [tuple(s) for c in t["cells"] for s in c["occupied_slots"]]
        assert (
            len(slots)
            == len(set(slots))
            == (len(t["x_boundaries"]) - 1) * (len(t["y_boundaries"]) - 1)
        )
    for rel in gold["focused_relations"]:
        t = next(t for t in gold["tables"] if t["id"] == rel["table"])
        a, b = [
            next(c for c in t["cells"] if [c["row"], c["column"]] == rel[k])
            for k in ("container", "member")
        ]
        ar = set(range(*a["row_range"]))
        br = set(range(*b["row_range"]))
        assert (br <= ar) if rel["expected"] == "row_range_contains" else not ar & br


def test_text_crossing_gap_blocks_bridge():
    lines = (
        line("horizontal", 0, 0, 100, 0),
        line("horizontal", 50, 0, 49.4, 1),
        line("horizontal", 50, 50.6, 100, 2),
        line("horizontal", 100, 0, 100, 3),
        line("vertical", 0, 0, 100, 4),
        line("vertical", 100, 0, 100, 5),
    )
    span = TextSpanIR(
        span_id="crossing",
        source=SourceRef(document_id="test", page_number=1, bbox=bbox(45, 45, 55, 55)),
        text_raw="X",
        line_index=0,
        font="test",
        font_size=10,
        flags=0,
    )
    grid = GridIntersectionAnalyzer(GridPolicy()).analyze(
        TableRegion(bbox(0, 0, 100, 100), lines), (span,)
    )
    edge = next(b for b in grid.boundaries if b.axis == "horizontal" and b.row == 1)
    assert edge.status == "uncertain"
    assert edge.text_conflict_span_ids == ("crossing",)


def test_open_outer_is_inferred_not_observed_and_missing_interior_stays_absent():
    lines = tuple(line("horizontal", y, 0, 100, i) for i, y in enumerate([0, 25, 50, 75, 100]))
    lines += (line("vertical", 40, 0, 100, 5), line("vertical", 60, 0, 25, 6))
    grid = GridIntersectionAnalyzer(GridPolicy()).analyze(TableRegion(bbox(0, 0, 100, 100), lines))
    outer = [b for b in grid.boundaries if b.axis == "vertical" and b.column in (0, 3)]
    assert all(b.status == "inferred" and b.coverage == 0 for b in outer)
    assert all(
        b.status == "absent"
        for b in grid.boundaries
        if b.axis == "vertical" and b.column == 2 and b.row > 0
    )


def test_adaptive_pixels_reject_fill_interior_and_recover_pale_line():
    from doc_understanding.parser.physical.line_detector import LineDetector, RasterEvidence
    from tests.support import sample_ir

    pixels = bytearray([255] * 200 * 110)
    for y in range(20, 50):
        for x in range(20, 180):
            pixels[y * 200 + x] = 60
    for x in range(20, 180):
        pixels[80 * 200 + x] = 220
    image = RasterEvidence(200, 110, bytes(pixels), (1, 0, 0, 1, 0, 0))
    page = sample_ir().pages[0]
    lines = LineDetector(GridPolicy()).raster(page, image)
    assert any(segment.axis == "horizontal" and abs(segment.position - 80) < 1 for segment in lines)
    assert not any(segment.axis == "horizontal" and 25 < segment.position < 45 for segment in lines)
    assert any(segment.evidence_kind == "fill_transition" for segment in lines)
    assert len(lines) < 20


def test_collinear_fragments_are_combined_for_region_evidence():
    from doc_understanding.parser.physical.region_assessor import longest_collinear_run

    fragments = [
        line("horizontal", 10, 0, 30, 0),
        line("horizontal", 10, 30, 60, 1),
        line("horizontal", 10, 60, 100, 2),
    ]
    assert longest_collinear_run(fragments, 0.75) == 100


def test_region_assessor_keeps_rejection_evidence():
    from doc_understanding.parser.physical.region_assessor import assess_region
    from tests.support import sample_ir

    tiny = TableRegion(bbox(0, 0, 12, 12), (line("horizontal", 0, 0, 12, 0),))
    result = assess_region(
        tiny, sample_ir().pages[0].model_copy(update={"blocks": ()}), GridPolicy()
    )
    assert result.decision == "rejected"
    assert result.lines == tiny.lines


def test_metrics_do_not_count_unresolved_or_duplicate_as_correct_merge(tmp_path):
    from doc_understanding.evaluation.parser.golden import evaluate

    gc = dict(
        id="cell",
        row=0,
        column=0,
        rowspan=2,
        colspan=1,
        bbox=[0, 0, 100, 100],
        occupied_slots=[[0, 0], [1, 0]],
    )
    gold = dict(
        complete_detection_pages=[1],
        provenance=dict(coordinate_tolerance_points=1.5, review_status="test"),
        tables=[dict(id="t", page=1, bbox=[0, 0, 100, 100], cells=[gc], boundaries=[])],
    )
    pc = dict(
        source_bbox=gc["bbox"],
        rowspan=None,
        colspan=None,
        status="unresolved",
        occupied_slots=gc["occupied_slots"],
    )
    table = dict(page_number=1, bounding_box=gc["bbox"], cells=[pc], boundaries=[])
    (tmp_path / "page-001.json").write_text(
        json.dumps(dict(tables=[table, table])), encoding="utf-8"
    )
    metrics = evaluate(tmp_path, gold)
    assert metrics["detection_precision"] == 0.5
    assert metrics["cell_geometry_accuracy"] == 1
    assert metrics["merged_span_accuracy"] == 0
    assert metrics["unresolved_rate"] == 1
    assert metrics["boundary_precision"] is None
