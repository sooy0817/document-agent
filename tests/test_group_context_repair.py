from copy import deepcopy

from doc_understanding.agents.semantic_resolver.group_context import propose_intervals


def fixture():
    cells = []
    for r, (label, value) in enumerate(
        [
            ("header", "value"),
            ("groupA", ""),
            ("leafA", "1"),
            ("sumA", "1"),
            ("groupB", ""),
            ("leafB", "2"),
        ]
    ):
        cells.append(
            dict(
                cell_id=f"r{r}",
                row_index=r,
                column_index=0,
                colspan=2 if r == 3 else 1,
                original_text=label,
                status="unresolved",
            )
        )
        cells.append(
            dict(
                cell_id=f"v{r}",
                row_index=r,
                column_index=2,
                colspan=1,
                original_text=value,
                status="resolved",
            )
        )
    return {"cells": cells}, {"panels": [{"header_rows": 1, "stub_columns": 1}]}


def test_label_replacement_does_not_change_edges_and_preserves_inputs():
    t, s = fixture()
    before = deepcopy(t)
    a = propose_intervals(t, s)
    assert t == before
    for c in t["cells"]:
        if c["column_index"] == 0:
            c["original_text"] = "random_label"
    b = propose_intervals(t, s)
    assert a == b
    assert [(p["child_cell_id"], p["parent_cell_id"]) for p in a["proposals"]] == [
        ("r2", "r1"),
        ("r3", "r1"),
    ]
    assert all(p["state"] == "inferred" for p in a["proposals"])
    assert any(x.get("cell_id") == "r5" and "FINAL_ROW" in x["reason"] for x in a["abstentions"])


def test_parallel_panels_abstain():
    t, s = fixture()
    s["panels"] *= 2
    assert not propose_intervals(t, s)["proposals"]


def test_unpopulated_following_rows_do_not_create_group():
    t, s = fixture()
    for c in t["cells"]:
        if c["column_index"] > 0:
            c["original_text"] = ""
    assert not propose_intervals(t, s)["proposals"]
