import pytest

from doc_understanding.evaluation.parser.tree_accuracy import evaluate_table, iou, path
from doc_understanding.evaluation.sample_unseen import select


def inventory(n=30):
    return [
        dict(
            document_id=f"d{i:02}",
            institution_id=f"i{i}",
            institution_name=f"테스트{i}",
            period=str(2024 + i % 2),
            document_type=["audit", "disclosure"][i % 2],
            format_family=["A", "B", "C"][i % 3],
            native_text_group="native",
            source_path=f"/not-opened/{i}.pdf",
            sha256=f"{i:064x}",
            duplicate_cluster=f"u{i}",
        )
        for i in range(n)
    ]


FREEZE = {
    "files": {"resolver": "hash"},
    "model_versions": {"text": "frozen"},
    "settings": {"threshold": 0.7},
}


def test_sampler_reproducible_under_inventory_reordering():
    rows = inventory()
    assert select(rows, FREEZE) == select(list(reversed(rows)), FREEZE)
    assert select(rows, FREEZE)["selected_count"] == 20
    assert select(rows, FREEZE, seed=2)["selected"] != select(rows, FREEZE)["selected"]


def test_duplicate_and_holdout_leakage_components_excluded():
    rows = inventory()
    rows[0]["institution_name"] = "남평농협"
    rows[1]["sha256"] = rows[0]["sha256"]
    rows[2]["duplicate_cluster"] = rows[1]["duplicate_cluster"]
    rows[3]["institution_id"] = rows[4]["institution_id"]
    out = select(rows, {**FREEZE, 'excluded_institution_names': ['남평']})["selected"]
    assert not {"d00", "d01", "d02"} & {r["document_id"] for r in out}
    assert len({r["institution_id"] for r in out}) == len(out)


def test_sampler_no_silent_replacement_for_shortfall_or_missing_metadata():
    assert select(inventory(2), FREEZE)["shortfall"] == 18
    with pytest.raises(ValueError):
        select(inventory(), {})
    rows = inventory()
    rows[0]["sha256"] = "unknown"
    with pytest.raises(ValueError):
        select(rows, FREEZE)


def test_cycles_and_geometry():
    assert path("a", {"a": "b", "b": "a"})[0] == "CYCLE"
    assert iou([0, 0, 2, 2], [0, 0, 2, 2]) == 1
    assert iou([0, 0, 1, 1], [2, 2, 3, 3]) == 0


def test_unbound_child_cannot_be_scored_as_correct_root():
    gold = {
        "id": "g",
        "page": 1,
        "cells": [{"id": "g0", "bbox": [0, 0, 1, 1], "text": "X"}],
        "rows": [{"cell": "g0", "parent": None}],
        "columns": [],
        "mappings": [],
    }
    result = evaluate_table(
        gold, {"cells": []}, {"row_tree": [], "column_tree": [], "cell_mappings": []}, {}
    )
    assert result["metrics"]["exact_row_path"]["correct"] == 0
    assert result["metrics"]["column_path"]["value"] is None


def test_wrong_parent_reduces_precision_and_recall():
    cells = [{"id": str(i), "bbox": [i, 0, i + 1, 1], "text": str(i)} for i in range(3)]
    gold = {
        "id": "g",
        "page": 1,
        "cells": cells,
        "rows": [
            {"cell": "0", "parent": None},
            {"cell": "1", "parent": "0"},
            {"cell": "2", "parent": None},
        ],
        "columns": [],
        "mappings": [],
    }
    physical = {
        "cells": [
            {
                "cell_id": c["id"],
                "source_bbox": dict(zip(["x0", "y0", "x1", "y1"], c["bbox"])),
                "original_text": c["text"],
            }
            for c in cells
        ]
    }
    result = evaluate_table(
        gold, physical, {"row_tree": [], "column_tree": [], "cell_mappings": []}, {"1": "2"}
    )
    assert result["metrics"]["parent_precision"]["value"] == 0
    assert result["metrics"]["parent_recall"]["value"] == 0
