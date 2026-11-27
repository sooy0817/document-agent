"""Public reader never consults internal IDs. Numbers are local to each table."""
from copy import deepcopy
import json

import pytest

from doc_understanding.evaluation.parser.tabledoc_readback import readback


def projection():
    table = dict(table_id=1, page_no=1, grid=[
        dict(cell_id=1, role="row_label", text="A"), dict(cell_id=2, role="column_header", text="B"),
        dict(cell_id=3, role="value", text="a sentence", bbox=[1, 2, 3, 4])],
        row_nodes=[dict(node_id=1, parent=None, cell_id=1, label_raw="A")],
        column_nodes=[dict(node_id=2, parent=None, cell_id=2, label_raw="B")],
        paths=[dict(cell_id=3, row_node=1, column_node=2, state="inferred")])
    other = deepcopy(table)
    other.update(table_id=2, page_no=2)
    other["column_nodes"][0]["label_raw"] = "other B"
    return dict(format="tabledoc/0.3", sections=[dict(section_id=1, parent=None),
                dict(section_id=2, parent=1)], pages=[
        dict(page_no=1, items=[dict(kind="table", position=1, section_id=1, table_id=1)]),
        dict(page_no=2, items=[dict(kind="table", position=1, section_id=2, table_id=2, context=[3]),
                               dict(kind="note", position=2, section_id=2, content_id=3, text="note")])],
        tables=[table, other], links=[
            dict(relation="note", scope=scope, state="ambiguous", content_id=3, table_id=2, node=node)
            for scope, node in (("table", None), ("row", 1), ("column", 2))],
        table_links=[dict(from_table=1, to_table=2, kind="page_continuation", state="candidate", reasons=["geometry"])])


def test_public_only_note_targets_and_section_descendants():
    v = readback(projection())
    assert v["values"][2, 3] == ("a sentence", 2, (1, 2, 3, 4), ((1, "A"),), ((2, "other B"),), "inferred")
    assert v["notes"] == [(3, 2, None, "table", "ambiguous", None),
                          (3, 2, ("row", ((1, "A"),)), "row", "ambiguous", None),
                          (3, 2, ("column", ((2, "other B"),)), "column", "ambiguous", None)]
    assert v["members"][1] == [("table", 1)]
    assert v["subtrees"][1] == [("table", 1), ("table", 2), ("content", 3)]
    assert v["context"][2] == (3,)
    assert v["joins"] == [(1, 2, 1, 2, "page_continuation", "candidate", ("geometry",))]


def test_unknown_or_cross_axis_leaf_is_not_guessed_from_nearby_text():
    public = projection()
    public["tables"][0]["paths"][0]["column_node"] = 1
    with pytest.raises(ValueError, match="wrong axis"):
        readback(public)


def test_04_refs_are_table_scoped_and_reasons_can_be_overridden():
    public = projection()
    public["format"] = "tabledoc/0.4"
    link = public["table_links"][0]
    link.update(header_cells=[[1, 2]], candidate_tables=[1], paths=[
        dict(cell_id=3, row=[[2, 1]], column=[[1, 2]], state="inferred"),
        dict(cell_id=1, row=[], column=[], row_candidates=[[1, 1]], state="unresolved", reasons=[])])
    headers, _, candidates, paths = readback(public)["details"][1, 2]
    assert (headers, candidates) == (((1, 2),), (1,))
    assert paths[3] == (((2, 1),), ((1, 2),), (), "inferred", ("geometry",))
    assert paths[1] == ((), (), ((1, 1),), "unresolved", ())
    link["paths"][0]["column"] = [[3, 2]]
    with pytest.raises(ValueError, match="missing linked cell"):
        readback(public)


def test_export_and_oracle_distinguish_table_row_and_column_notes(tmp_path):
    import pymupdf

    from doc_understanding.parser.assembly.document import build_from_run
    from doc_understanding.parser.contracts import ContextLink
    from doc_understanding.parser.pipeline import prepare
    from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer
    from doc_understanding.export.clean import clean_document
    from doc_understanding.export.models import CleanDocument
    from scripts.audit_tabledoc_readback import compare
    from tests.semantic_fixtures import fixture_pdf

    pdf = tmp_path / "note.pdf"
    with pymupdf.open(stream=fixture_pdf("laboratory"), filetype="pdf") as source:
        source[0].insert_text((24, 245), "Note: follow the instructions.")
        source.save(pdf)
    run, _ = prepare(pdf, tmp_path)
    doc = build_from_run(run)
    table, = doc.physical_tables
    structure = SemanticTableAnalyzer().analyze(table, header_rows=2, stub_columns=2)
    note = next(c for c in doc.contents if "follow the instructions" in (c.text_raw or ""))
    scopes = [("table", table.table_id), ("row", structure.row_tree[-1].node_id),
              ("column", structure.column_tree[-1].node_id)]
    links = tuple(ContextLink(source=note.source_references[0], relation="note", scope=scope,
                              target_id=target, status="ambiguous") for scope, target in scopes)
    doc = doc.model_copy(update={"table_structure": (structure,), "context_links": links})
    output = json.dumps(clean_document(doc))
    CleanDocument.model_validate_json(output)
    view = readback(json.loads(output))
    assert [node[0] if node else "table" for _, _, node, *_ in view["notes"]] == ["table", "row", "column"]
    assert not compare(doc, view)["mismatches"]
    mutated = json.loads(output)
    mutated["links"][2]["node"] = mutated["links"][1]["node"]
    assert "note_links" in compare(doc, readback(mutated))["mismatches"]
