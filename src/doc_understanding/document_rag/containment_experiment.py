"""Opt-in, source-only containment experiment. Never imported by production build.

No document names, questions, Gold, institution metadata or page-specific policy
is accepted. Original IRs and the baseline linker are never modified.
"""

from collections import Counter
import hashlib
from typing import Literal

from doc_understanding.domain.models import BBox, EvidenceModel
from doc_understanding.table_understanding.geometry import cluster, intervals_union, overlap
from doc_understanding.table_understanding.line_detector import LineDetector
from doc_understanding.table_understanding.physical_models import GridPolicy
from doc_understanding.table_understanding.region_detector import touches
from .nested_content import (
    ContentItem,
    NestedContent,
    build_nested_content,
    contains,
    same_box,
    order_items,
    snapshot_hash,
    _union,
)


class CandidateDecision(EvidenceModel):
    child_ref_id: str
    page_number: int
    decision: Literal["retain", "exclude_from_assembly", "unresolved"]
    reasons: tuple[str, ...]
    source_drawing_ids: tuple[str, ...] = ()
    crossing_span_ids: tuple[str, ...] = ()


class LayoutFrame(EvidenceModel):
    frame_id: str
    page_number: int
    bbox: BBox
    source_line_ids: tuple[str, ...]
    source_drawing_ids: tuple[str, ...]
    side_coverage: tuple[float, ...]
    parent_kind: Literal["layout_frame"] = "layout_frame"
    semantic_status: Literal["not_validated"] = "not_validated"


class FrameContent(EvidenceModel):
    frame_id: str
    page_number: int
    child_table_ids: tuple[str, ...]
    items: tuple[ContentItem, ...]
    source_span_ids: tuple[str, ...]
    ignored_whitespace_span_ids: tuple[str, ...]
    uncovered_span_ids: tuple[str, ...]
    duplicate_span_ids: tuple[str, ...]
    outside_parent_span_ids: tuple[str, ...]
    reasons: tuple[str, ...]
    status: Literal["geometrically_linked", "unresolved"]
    semantic_status: Literal["not_validated"] = "not_validated"


class ExperimentResult(EvidenceModel):
    schema_version: Literal["containment-experiment/1.0"] = "containment-experiment/1.0"
    variant: Literal["E0", "E1", "E2", "E3"]
    document_id: str
    canonical_sha256: str
    baseline: NestedContent
    effective_cell_content: NestedContent
    decisions: tuple[CandidateDecision, ...]
    frames: tuple[LayoutFrame, ...]
    frame_contents: tuple[FrameContent, ...]
    policy: GridPolicy
    facts_published: Literal[0] = 0


def _component_groups(lines, policy):
    if len(lines) > policy.max_lines:
        raise ValueError("LINE_BUDGET_EXCEEDED")
    parent = list(range(len(lines)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, line in enumerate(lines):
        for j in range(i):
            if touches(line, lines[j], policy.tolerance):
                parent[root(i)] = root(j)
    groups = {}
    for i, line in enumerate(lines):
        groups.setdefault(root(i), []).append(line)
    return groups.values()


def _coverage(lines, axis, pos, start, end, tol):
    intervals = [
        (max(start, line.start), min(end, line.end))
        for line in lines
        if line.axis == axis
        and abs(line.position - pos) <= tol
        and min(end, line.end) > max(start, line.start)
    ]
    return sum(b - a for a, b in intervals_union(intervals)) / max(end - start, 1e-9)


def find_layout_frames(page, physical, policy=GridPolicy()):
    """Recognize closed source frames discarded by the >=3 grid-band criterion."""
    lines = LineDetector(policy).vector(page)
    result = []
    for group in _component_groups(lines, policy):
        xs = cluster([line.position for line in group if line.axis == "vertical"], policy.tolerance)
        ys = cluster(
            [line.position for line in group if line.axis == "horizontal"], policy.tolerance
        )
        if len(xs) != 2 or len(ys) != 2:
            continue
        box = BBox(x0=xs[0], y0=ys[0], x1=xs[1], y1=ys[1])
        cover = tuple(
            _coverage(group, axis, pos, lo, hi, policy.tolerance)
            for axis, pos, lo, hi in [
                ("horizontal", ys[0], xs[0], xs[1]),
                ("horizontal", ys[1], xs[0], xs[1]),
                ("vertical", xs[0], ys[0], ys[1]),
                ("vertical", xs[1], ys[0], ys[1]),
            ]
        )
        # Same observation tolerance as the existing physical grid, not tuned to a PDF.
        if min(cover) < policy.minimum_gap_coverage:
            continue
        if any(same_box(box, c.source.bbox) for t in page.tables for c in t.cells):
            continue
        if any(same_box(box, t.bounding_box) for t in physical):
            continue
        ids = tuple(sorted(line.line_id for line in group))
        suffix = hashlib.sha256("\n".join(ids).encode()).hexdigest()[:16]
        result.append(
            LayoutFrame(
                frame_id=f"{page.page_id}/layout-{suffix}",
                page_number=page.page_number,
                bbox=box,
                source_line_ids=ids,
                source_drawing_ids=tuple(sorted({line.source.block_id for line in group})),
                side_coverage=cover,
            )
        )
    return tuple(result)


def assess_candidate(page, table, physical, policy=GridPolicy()):
    matches = [p for p in physical if same_box(p.bounding_box, table.source.bbox)]
    if matches:
        return CandidateDecision(
            child_ref_id=table.table_candidate_id,
            page_number=page.page_number,
            decision="retain",
            reasons=("PHYSICAL_MATCH_PRESENT_NOT_SEMANTIC_APPROVAL",),
        )
    b = table.source.bbox
    # Native support alone is not a truth label. Lack of physical support is not rejection.
    fill_ids = set()
    for block in page.blocks:
        if block.drawing_stroked is not False:
            continue
        for command in block.drawing_commands:
            if command.operator == "re" and len(command.coordinates) >= 4:
                x0, y0, x1, y1 = command.coordinates[:4]
                if same_box(b, BBox(x0=x0, y0=y0, x1=x1, y1=y1)):
                    fill_ids.add(block.block_id)
    # Short strokes also contradict a pure text-background explanation. This
    # evidence check does not change the physical reconstructor's 12 pt policy.
    lines = LineDetector(policy.model_copy(update={"min_line_length": 1e-9})).vector(page)
    stroke_support = any(
        (
            line.axis == "horizontal"
            and b.y0 - 1.5 <= line.position <= b.y1 + 1.5
            and min(b.x1, line.end) - max(b.x0, line.start) > 1.5
        )
        or (
            line.axis == "vertical"
            and b.x0 - 1.5 <= line.position <= b.x1 + 1.5
            and min(b.y1, line.end) - max(b.y0, line.start) > 1.5
        )
        for line in lines
    )
    internal = {c.source.bbox.x1 for c in table.cells if b.x0 + 1.5 < c.source.bbox.x1 < b.x1 - 1.5}
    crossing = {
        s.span_id
        for block in page.blocks
        for s in block.spans
        if overlap(s.source.bbox, b) > 0
        and any(s.source.bbox.x0 + 1.5 < x < s.source.bbox.x1 - 1.5 for x in internal)
    }
    exclude = table.row_count == 1 and fill_ids and crossing and not stroke_support
    diagnostics = ["NATIVE_ONLY_NOT_DISPROVEN"]
    if not fill_ids:
        diagnostics.append("NO_MATCHING_FILL_RECTANGLE")
    if stroke_support:
        diagnostics.append("STROKE_SUPPORT_PRESENT")
    if not crossing:
        diagnostics.append("NO_SOURCE_SPAN_CROSSES_COLUMN")
    if table.row_count != 1:
        diagnostics.append("NOT_SINGLE_ROW_BACKGROUND")
    return CandidateDecision(
        child_ref_id=table.table_candidate_id,
        page_number=page.page_number,
        decision="exclude_from_assembly" if exclude else "unresolved",
        reasons=("FILL_BACKGROUND_SPLITS_SOURCE_TEXT",) if exclude else tuple(diagnostics),
        source_drawing_ids=tuple(sorted(fill_ids)),
        crossing_span_ids=tuple(sorted(crossing)),
    )


def _within_point(span, box):
    b = span.source.bbox
    return box.x0 <= (b.x0 + b.x1) / 2 <= box.x1 and box.y0 <= (b.y0 + b.y1) / 2 <= box.y1


def _frame_content(doc, page, frame, children):
    spans = {s.span_id: s for b in page.blocks for s in b.spans}
    expected = {sid for sid, s in spans.items() if _within_point(s, frame.bbox)}
    items = [
        ContentItem(
            kind="table",
            ref_id=t.table_id,
            bbox=t.bounding_box,
            span_ids=tuple(sorted({s for c in t.cells for s in c.span_refs})),
        )
        for t in children
    ]
    child_spans = {s for i in items for s in i.span_ids}
    white = {s for s in expected if not spans[s].text_raw.strip()}
    reasons = set()
    if any(b.kind == "image" and overlap(b.source.bbox, frame.bbox) > 0 for b in page.blocks):
        reasons.add("IMAGE_CONTENT_ORDER_UNRESOLVED")
    for c in doc.contents:
        refs = c.source_references
        if c.kind == "table" or not refs or refs[0].page_number != page.page_number:
            continue
        ids = {r.span_id for r in refs if r.span_id}
        if not ids.intersection(expected - child_spans - white):
            continue
        if not ids.issubset(expected) or ids.intersection(child_spans):
            reasons.add("CONTENT_SPAN_OWNERSHIP_CONFLICT")
            continue
        items.append(
            ContentItem(
                kind="content", ref_id=c.content_id, bbox=_union(refs), span_ids=tuple(sorted(ids))
            )
        )
    ordered, order_reasons = order_items(items)
    reasons.update(order_reasons)
    counts = Counter(s for i in ordered for s in i.span_ids)
    missing, outside = expected - set(counts) - white, set(counts) - expected
    duplicates = {s for s, n in counts.items() if n > 1}
    for value, code in [
        (missing, "UNCOVERED_PARENT_SPANS"),
        (outside, "ITEM_SPANS_OUTSIDE_PARENT"),
        (duplicates, "DUPLICATE_SPAN_OWNERSHIP"),
    ]:
        if value:
            reasons.add(code)
    return FrameContent(
        frame_id=frame.frame_id,
        page_number=page.page_number,
        child_table_ids=tuple(t.table_id for t in children),
        items=ordered,
        source_span_ids=tuple(sorted(expected)),
        ignored_whitespace_span_ids=tuple(sorted(white - set(counts))),
        uncovered_span_ids=tuple(sorted(missing)),
        duplicate_span_ids=tuple(sorted(duplicates)),
        outside_parent_span_ids=tuple(sorted(outside)),
        reasons=tuple(sorted(reasons)),
        status="unresolved" if reasons else "geometrically_linked",
    )


def run_experiment(doc, variant):
    if variant not in {"E0", "E1", "E2", "E3"}:
        raise ValueError("UNKNOWN_VARIANT")
    before = snapshot_hash(doc)
    baseline = build_nested_content(doc)
    effective, decisions = baseline, []
    policy = GridPolicy()
    if variant in {"E2", "E3"}:
        ids = {c.native_table_id for c in baseline.candidates if c.native_table_id}
        for page in doc.document_ir.pages:
            physical = [p for p in doc.physical_tables if p.page_number == page.page_number]
            decisions.extend(
                assess_candidate(page, t, physical, policy)
                for t in page.tables
                if t.table_candidate_id in ids
            )
        excluded = {d.child_ref_id for d in decisions if d.decision == "exclude_from_assembly"}
        if excluded:
            # Temporary in-memory candidate view only; never serialize or replace original IR.
            # Table blocks are not consumed by the unchanged linker. All text blocks remain.
            pages = tuple(
                p.model_copy(
                    update={
                        "tables": tuple(t for t in p.tables if t.table_candidate_id not in excluded)
                    }
                )
                for p in doc.document_ir.pages
            )
            view = doc.model_copy(
                update={"document_ir": doc.document_ir.model_copy(update={"pages": pages})}
            )
            effective = build_nested_content(view).model_copy(update={"canonical_sha256": before})
    frames, contents = [], []
    if variant in {"E1", "E3"}:
        for page in doc.document_ir.pages:
            physical = [p for p in doc.physical_tables if p.page_number == page.page_number]
            candidates = find_layout_frames(page, physical, policy)
            selected = {}
            for child in physical:
                if len(child.cells) < 2:
                    continue
                # Existing native cell owner takes precedence over a larger layout wrapper.
                if any(
                    contains(child.bounding_box, c.source.bbox)
                    for t in page.tables
                    for c in t.cells
                ):
                    continue
                parents = [f for f in candidates if contains(child.bounding_box, f.bbox)]
                winners = [
                    f
                    for f in parents
                    if all(f.frame_id == p.frame_id or contains(f.bbox, p.bbox) for p in parents)
                ]
                if len(winners) == 1:
                    selected.setdefault(winners[0].frame_id, []).append(child)
            for frame in candidates:
                if frame.frame_id in selected:
                    frames.append(frame)
                    contents.append(_frame_content(doc, page, frame, selected[frame.frame_id]))
    if before != snapshot_hash(doc):
        raise AssertionError("ORIGINAL_DOCUMENT_MUTATED")
    return ExperimentResult(
        variant=variant,
        document_id=doc.document_id,
        canonical_sha256=before,
        baseline=baseline,
        effective_cell_content=effective,
        decisions=tuple(decisions),
        frames=tuple(frames),
        frame_contents=tuple(contents),
        policy=policy,
    )
