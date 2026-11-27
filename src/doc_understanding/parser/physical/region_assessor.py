# 역할: 기하 근거로 비표 영역을 판정한다. 기관명·항목명·페이지 번호를 규칙으로 쓰지 않는다.
"""Conservative geometric negatives. No title, account name or page-number rules."""

from doc_understanding.parser.physical.geometry import cluster
from doc_understanding.parser.physical.physical_models import RegionAssessment


# 허용 오차 내 같은 위치의 선 조각을 이어 가장 긴 연속 길이를 계산한다.
def longest_collinear_run(lines, tolerance):
    longest = 0
    for position in cluster([segment.position for segment in lines], tolerance):
        intervals = sorted(
            (segment.start, segment.end)
            for segment in lines
            if abs(segment.position - position) <= tolerance
        )
        start = end = None
        for a, b in intervals:
            if end is not None and a <= end + tolerance:
                end = max(end, b)
            else:
                start, end = a, b
            longest = max(longest, end - start)
    return longest


# 선 연속성·텍스트 정렬 등의 강한 반례로 도표성 영역을 걸러낸다. 반례가 없으면 표 후보로 유지한다.
def assess_region(region, page, policy):
    box = region.bounding_box
    width, height = box.x1 - box.x0, box.y1 - box.y0
    hs = [segment for segment in region.lines if segment.axis == "horizontal"]
    vs = [segment for segment in region.lines if segment.axis == "vertical"]
    spans = [
        s
        for b in page.blocks
        for s in b.spans
        if box.x0 <= (s.source.bbox.x0 + s.source.bbox.x1) / 2 <= box.x1
        and box.y0 <= (s.source.bbox.y0 + s.source.bbox.y1) / 2 <= box.y1
    ]
    numeric = [
        s
        for s in spans
        if sum(ch.isdigit() for ch in s.text_raw) > sum(ch.isalpha() for ch in s.text_raw)
    ]
    # Repeated native x alignments are supplemental evidence, never OCR for raster input.
    starts = cluster([s.source.bbox.x0 for s in spans], policy.tolerance * 3)
    repeated = sum(
        sum(abs(s.source.bbox.x0 - x) <= policy.tolerance * 3 for s in spans) >= 2 for x in starts
    )
    h = longest_collinear_run(hs, policy.tolerance) / max(width, 1)
    v = longest_collinear_run(vs, policy.tolerance) / max(height, 1)
    features = dict(
        horizontal_extent=h,
        vertical_extent=v,
        row_positions=len(cluster([segment.position for segment in hs], policy.tolerance)),
        column_positions=len(cluster([segment.position for segment in vs], policy.tolerance)),
        native_text_spans=len(spans),
        numeric_span_fraction=len(numeric) / max(1, len(spans)),
        repeated_text_x_alignments=repeated,
    )
    reason = None
    if width < 20 and height < 20 and len(spans) < 2:
        reason = "SMALL_GRAPHIC_WITHOUT_TABULAR_TEXT"
    elif (
        hs
        and vs
        and h < 0.65
        and v < 0.55
        and len(numeric) / max(1, len(spans)) < 0.5
        and features["row_positions"] >= 3
        and features["column_positions"] >= 3
    ):
        reason = "DISCONNECTED_BOX_LAYOUT_WITHOUT_REPEATED_GRID_SUPPORT"
    elif (
        spans
        and v < 0.5
        and not numeric
        and repeated == 0
        and features["row_positions"] >= 4
        and features["column_positions"] >= 3
    ):
        reason = "CONNECTOR_LAYOUT_WITHOUT_REPEATED_TEXT_ALIGNMENT"
    return RegionAssessment(
        bounding_box=box,
        decision="rejected" if reason else "retained",
        reason=reason or "NO_STRONG_NEGATIVE",
        features=features,
        lines=region.lines if reason else (),
    )
