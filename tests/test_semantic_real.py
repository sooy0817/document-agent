"""Real-source review assertions are explicitly NOT human-certified goldens."""

import json
from pathlib import Path

import pytest

from doc_understanding.parser.physical.physical_models import PhysicalTableIR
from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer, path_for

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/quality-final/acbb717f-99af-4e98-8163-aede4f2f578e/physical"
CASES = json.loads(
    (ROOT / "tests/golden/ganggu-semantic-review-cases.json").read_text(encoding="utf8")
)
pytestmark = pytest.mark.skipif(
    not DATA.exists(), reason="Local real PDF artifacts unavailable; not a synthetic replacement"
)


def load(page, table):
    return PhysicalTableIR.model_validate_json(
        (DATA / f"page-{page:03d}-table-{table:03d}.json").read_bytes()
    )


def norm(s):
    return "".join((s or "").split())


@pytest.mark.parametrize("case", CASES["row_paths"])
def test_source_reviewed_row_path(case):
    result = SemanticTableAnalyzer().analyze(load(case["page"], case["table"]))
    idx = {n.node_id: n for n in result.row_tree}
    paths = [
        [norm(idx[i].label_raw) for i in path_for(n, result.row_tree)] for n in result.row_tree
    ]
    assert case["labels"] in paths


@pytest.mark.parametrize("case", CASES["forbidden_row_paths"])
def test_outside_merge_is_not_child(case):
    result = SemanticTableAnalyzer().analyze(load(case["page"], case["table"]))
    idx = {n.node_id: n for n in result.row_tree}
    assert case["labels"] not in [
        [norm(idx[i].label_raw) for i in path_for(n, result.row_tree)] for n in result.row_tree
    ]


@pytest.mark.parametrize("case", CASES["column_children"])
def test_source_reviewed_column_children(case):
    result = SemanticTableAnalyzer().analyze(load(case["page"], case["table"]))
    parent = next(n for n in result.column_tree if norm(n.label_raw) == case["parent"])
    children = [n for n in result.column_tree if n.parent_id == parent.node_id]
    assert [norm(n.label_raw) for n in children] == case["children"]
    assert len({n.node_id for n in children}) == len(children)


@pytest.mark.parametrize(
    "page,table", [(8, 0), (14, 0), (14, 1), (15, 0), (18, 0), (18, 1), (18, 2)]
)
def test_real_source_immutable_and_label_blind(page, table):
    physical = load(page, table)
    original = physical.model_dump_json()
    analyzer = SemanticTableAnalyzer()
    result = analyzer.analyze(physical)
    changed = physical.model_copy(
        update={
            "page_number": 99,
            "table_id": "arbitrary-table",
            "cells": tuple(
                c.model_copy(update={"original_text": f"opaque-{i}"})
                for i, c in enumerate(physical.cells)
            ),
        }
    )
    other = analyzer.analyze(changed)

    def topology(nodes):
        ids = {n.node_id: n.source_cell_id for n in nodes}
        return [
            (n.source_cell_id, ids.get(n.parent_id), n.row_range, n.column_range) for n in nodes
        ]

    assert topology(result.row_tree) == topology(other.row_tree)
    assert topology(result.column_tree) == topology(other.column_tree)
    assert original == physical.model_dump_json()
    assert result.physical_boundaries == physical.boundaries
    assert not any(i.severity == "error" for i in result.issues)


def test_real_collision_remains_unresolved():
    result = SemanticTableAnalyzer().analyze(load(14, 0))
    assert any(a.code == "PATH_COLLISION" for a in result.ambiguities)
    assert sum(m.evidence_state == "unresolved" for m in result.cell_mappings) >= 18
