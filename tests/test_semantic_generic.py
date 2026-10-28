import pytest
from pydantic import ValidationError
from doc_understanding.table_understanding.semantic import (
    SemanticTableAnalyzer,
    validate_hierarchy,
    vision_request,
)
from doc_understanding.table_understanding.semantic_models import VisionStructureProposal
from tests.semantic_fixtures import compound


def edges(nodes):
    index = {n.node_id: n.source_cell_id for n in nodes}
    return {(n.source_cell_id, index[n.parent_id]) for n in nodes if n.parent_id}


@pytest.mark.parametrize("kind", ["laboratory", "warehouse", "survey"])
def test_independent_complex_layout(kind):
    table, row_edges, col_edges = compound(kind)
    before = table.model_dump_json()
    result = SemanticTableAnalyzer().analyze(table)
    assert edges(result.row_tree) == row_edges
    assert edges(result.column_tree) == col_edges
    assert not result.issues
    assert all(m.row_path and m.column_path for m in result.cell_mappings)
    assert len({(m.row_path, m.column_path) for m in result.cell_mappings}) == len(
        result.cell_mappings
    )
    assert before == table.model_dump_json()


@pytest.mark.parametrize("kind", ["laboratory", "warehouse", "survey"])
def test_arbitrary_label_permutation(kind):
    a = SemanticTableAnalyzer().analyze(compound(kind)[0])
    b = SemanticTableAnalyzer().analyze(compound(kind, True)[0])
    assert edges(a.row_tree) == edges(b.row_tree)
    assert edges(a.column_tree) == edges(b.column_tree)
    assert [(m.cell_id, m.row_path, m.column_path) for m in a.cell_mappings] == [
        (m.cell_id, m.row_path, m.column_path) for m in b.cell_mappings
    ]


def test_uncertainty_propagates_and_validator_rejects_promotion():
    table = compound()[0]
    cell = next(c for c in table.cells if c.cell_id == "r2c0")
    table = table.model_copy(
        update={
            "cells": tuple(
                c.model_copy(update={"status": "unresolved"}) if c == cell else c
                for c in table.cells
            )
        }
    )
    result = SemanticTableAnalyzer().analyze(table)
    assert all(m.evidence_state == "unresolved" for m in result.cell_mappings)
    bad = result.model_copy(
        update={
            "row_tree": tuple(
                n.model_copy(update={"evidence_state": "observed"}) for n in result.row_tree
            )
        }
    )
    assert "EVIDENCE_PROMOTION" in {i.code for i in validate_hierarchy(table, bad)}


def test_no_ocr_no_text_semantics():
    table = compound()[0]
    table = table.model_copy(
        update={"cells": tuple(c.model_copy(update={"original_text": None}) for c in table.cells)}
    )
    result = SemanticTableAnalyzer().analyze(table)
    assert all(m.evidence_state == "unresolved" for m in result.cell_mappings)
    assert all(n.label_raw is None for n in result.row_tree + result.column_tree)


def test_duplicate_cycle_missing_source_rejected():
    table = compound()[0]
    result = SemanticTableAnalyzer().analyze(table)
    n = result.row_tree[0]
    bad = n.model_copy(update={"parent_id": n.node_id, "source_cell_id": "invented"})
    changed = result.model_copy(update={"row_tree": (bad, bad) + result.row_tree[1:]})
    codes = {i.code for i in validate_hierarchy(table, changed)}
    assert {"DUPLICATE_NODE_ID", "SOURCE_REF_MISSING"} <= codes
    cycle = result.model_copy(
        update={"row_tree": (n.model_copy(update={"parent_id": n.node_id}),) + result.row_tree[1:]}
    )
    assert "TREE_CYCLE" in {i.code for i in validate_hierarchy(table, cycle)}


def test_continuation_is_candidate_not_join():
    table = compound()[0]
    other = table.model_copy(update={"table_id": "next", "page_number": 2})
    result = SemanticTableAnalyzer().analyze(table, (other,))
    assert any(
        f.type == "continued_across_pages" and f.disposition == "candidate" for f in result.features
    )
    assert any(a.code == "CONTINUATION_AMBIGUOUS" for a in result.ambiguities)


def test_resolver_contract_no_call_no_generated_values():
    table = compound()[0]
    request = vision_request(table, SemanticTableAnalyzer().analyze(table))
    assert request.max_calls == 0
    with pytest.raises(ValidationError):
        VisionStructureProposal(
            target_id="r2c0",
            operation="propose_parent",
            evidence_cell_ids=("r2c0",),
            reason="test",
            value=10,
        )


@pytest.mark.parametrize("kind", ["laboratory", "warehouse", "survey"])
def test_synthetic_pdf_end_to_end(kind, tmp_path):
    from doc_understanding.domain.contracts import ExtractionRequest
    from doc_understanding.extraction.pymupdf_adapter import PyMuPDFExtractor
    from doc_understanding.orchestrator.workflow import extract_ir
    from doc_understanding.table_understanding.physical_grid import PhysicalGridReconstructor
    from tests.semantic_fixtures import fixture_pdf

    ir = extract_ir(
        ExtractionRequest(fixture_pdf(kind), f"{kind}.pdf"), PyMuPDFExtractor(), tmp_path
    )
    physical = PhysicalGridReconstructor().analyze_page(ir.document_id, ir.pages[0])
    assert len(physical.tables) == 1
    result = SemanticTableAnalyzer().analyze(physical.tables[0])
    _, expected_rows, expected_cols = compound(kind)

    def coordinate_edges(nodes):
        idx = {n.node_id: f"r{n.row_range[0]}c{n.column_range[0]}" for n in nodes}
        return {(idx[n.node_id], idx[n.parent_id]) for n in nodes if n.parent_id}

    assert coordinate_edges(result.row_tree) == expected_rows
    assert coordinate_edges(result.column_tree) == expected_cols


def test_flat_layout_is_hypothesis_not_certified():
    table = compound()[0]
    table = table.model_copy(update={"cells": tuple(c for c in table.cells if c.row_index != 0)})
    result = SemanticTableAnalyzer().analyze(table)
    assert result.analysis_state != "validated"
    assert any(a.code == "LAYOUT_HYPOTHESIS" for a in result.ambiguities)
    assert any(a.code == "HEADER_REGION_AMBIGUOUS" for a in result.ambiguities)
    assert all(m.evidence_state == "unresolved" for m in result.cell_mappings)


def test_parallel_panels_detected_without_cross_mapping():
    from doc_understanding.domain.models import BBox
    from doc_understanding.table_understanding.physical_models import GridBand

    table = compound()[0]
    right = []
    for c in table.cells:
        box = BBox(
            x0=c.source_bbox.x0 + 300,
            y0=c.source_bbox.y0,
            x1=c.source_bbox.x1 + 300,
            y1=c.source_bbox.y1,
        )
        right.append(
            c.model_copy(
                update={
                    "cell_id": "right/" + c.cell_id,
                    "source_bbox": box,
                    "column_index": c.column_index + 6,
                    "occupied_slots": tuple((r, k + 6) for r, k in c.occupied_slots),
                    "source": c.source.model_copy(
                        update={"bbox": box, "cell_id": "right/" + c.cell_id}
                    ),
                }
            )
        )
    table = table.model_copy(
        update={
            "cells": table.cells + tuple(right),
            "columns": tuple(
                GridBand(index=i, start=20 + i * 50, end=70 + i * 50) for i in range(12)
            ),
            "bounding_box": BBox(x0=20, y0=20, x1=620, y1=164),
        }
    )
    result = SemanticTableAnalyzer().analyze(table)
    assert any(f.type == "side_by_side_panels" for f in result.features)
    assert all(
        not m.row_path and not m.column_path and m.evidence_state == "unresolved"
        for m in result.cell_mappings
    )


def test_parent_outside_span_rejected():
    table = compound("warehouse")[0]
    result = SemanticTableAnalyzer().analyze(table)
    child = next(n for n in result.row_tree if n.source_cell_id == "r6c2")
    wrong = next(n for n in result.row_tree if n.source_cell_id == "r2c1")
    altered = result.model_copy(
        update={
            "row_tree": tuple(
                n.model_copy(update={"parent_id": wrong.node_id}) if n == child else n
                for n in result.row_tree
            )
        }
    )
    assert "PARENT_OCCUPANCY_CONFLICT" in {i.code for i in validate_hierarchy(table, altered)}


def test_future_proposal_unknown_ids_rejected():
    from doc_understanding.table_understanding.semantic import validate_proposal

    table = compound()[0]
    request = vision_request(table, SemanticTableAnalyzer().analyze(table))
    proposal = VisionStructureProposal(
        target_id="invented",
        operation="propose_parent",
        parent_id="invented2",
        evidence_cell_ids=("invented3",),
        reason="untrusted",
    )
    assert set(validate_proposal(request, proposal)) == {
        "UNKNOWN_TARGET",
        "UNKNOWN_PARENT",
        "UNKNOWN_OR_MISSING_EVIDENCE",
    }


def test_physical_boundary_status_is_preserved_and_propagated():
    from doc_understanding.table_understanding.physical_models import Boundary

    table = compound()[0]
    boundary = Boundary(
        boundary_id="uncertain",
        axis="vertical",
        row=2,
        column=0,
        position=20,
        start=68,
        end=164,
        coverage=0.4,
        status="uncertain",
        line_ids=(),
        evidence_level="text_conflict",
    )
    table = table.model_copy(
        update={
            "boundaries": (boundary,),
            "unresolved_boundaries": (boundary,),
            "cells": tuple(
                c.model_copy(update={"boundary_ids": ("uncertain",)}) if c.cell_id == "r2c0" else c
                for c in table.cells
            ),
        }
    )
    result = SemanticTableAnalyzer().analyze(table)
    assert result.physical_boundaries == (boundary,)
    assert result.physical_unresolved_boundaries == (boundary,)
    assert all(m.evidence_state == "unresolved" for m in result.cell_mappings)


def test_nonrectangular_occupancy_invalid():
    table = compound()[0]
    first = table.cells[0]
    table = table.model_copy(
        update={
            "cells": (first.model_copy(update={"occupied_slots": ((0, 0), (1, 1))}),)
            + table.cells[1:]
        }
    )
    result = SemanticTableAnalyzer().analyze(table)
    assert result.analysis_state == "invalid"
    assert "INVALID_SOURCE_OCCUPANCY" in {i.code for i in result.issues}
