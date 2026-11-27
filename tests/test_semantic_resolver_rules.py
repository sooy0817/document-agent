"""Independent non-financial logical expectations, not produced by the resolver."""

from doc_understanding.domain.models import BBox, SourceRef
from doc_understanding.agents.semantic_resolver.models import SemanticFeature
from doc_understanding.agents.semantic_resolver.features import number_marker, roles
from doc_understanding.agents.semantic_resolver.candidates import build_candidates


def feature(i, text, panel="panel", page=1, indent=0):
    return SemanticFeature(
        node_id=f"n{i}",
        source_node_id=None,
        source_cell_ids=(f"c{i}",),
        source_refs=(
            SourceRef(
                document_id="synthetic",
                page_number=page,
                bbox=BBox(x0=indent, y0=i * 10, x1=100, y1=i * 10 + 9),
                cell_id=f"c{i}",
            ),
        ),
        text_raw=text,
        axis="row",
        table_id="t",
        section_id="section",
        panel_id=panel,
        page_number=page,
        order=(page, i, 0),
        row_range=(i, i + 1),
        column_range=(0, 1),
        physical_evidence="observed",
        number=number_marker(text),
        indent=indent,
        role_candidates=roles(text),
    )


def pairs(candidates):
    return {
        (c.parent_id, c.child_id)
        for c in candidates
        if c.relation_type == "parent_child" and "number_sequence" in c.methods
    }


def test_relative_number_families_not_fixed_depth():
    a = [
        feature(i, t)
        for i, t in enumerate(
            ["I. Lab", "(1) Signals", "a. Optical", "1. Red", "2. Blue", "(2) Samples", "1. Water"]
        )
    ]
    assert pairs(build_candidates(a)) == {
        ("n0", "n1"),
        ("n1", "n2"),
        ("n2", "n3"),
        ("n2", "n4"),
        ("n0", "n5"),
        ("n5", "n6"),
    }
    b = [feature(i, t) for i, t in enumerate(["1. Museum", "a. Gallery", "(1) Painting"])]
    assert pairs(build_candidates(b)) == {("n0", "n1"), ("n1", "n2")}


def test_marker_rename_keeps_relationships():
    a = [feature(i, t) for i, t in enumerate(["I. Plants", "a. Trees", "1. Oak"])]
    b = [feature(i, t) for i, t in enumerate(["I. ZYX", "a. QQQ", "1. ABC"])]
    assert pairs(build_candidates(a)) == pairs(build_candidates(b))


def test_unnumbered_bullets_are_children():
    nodes = [
        feature(i, t)
        for i, t in enumerate(["Equipment", "- Microscope", "- Pipette", "Personnel", "- Curator"])
    ]
    c = build_candidates(nodes)
    assert {(x.parent_id, x.child_id) for x in c if "bullet_group" in x.methods} == {
        ("n0", "n1"),
        ("n0", "n2"),
        ("n3", "n4"),
    }


def test_parentheses_are_relations_not_deleted_values():
    nodes = [feature(i, t) for i, t in enumerate(["1. Specimen", "(or Reference)", "(correction)"])]
    c = build_candidates(nodes)
    assert any(x.relation_type == "alternative_label" and x.child_id == "n1" for x in c)
    assert any(x.relation_type == "related_adjustment" and x.child_id == "n2" for x in c)


def test_panels_and_sections_do_not_share_stack():
    nodes = [
        feature(0, "I. West", "left"),
        feature(1, "I. East", "right"),
        feature(2, "1. Room", "left"),
        feature(3, "1. Room", "right"),
    ]
    assert pairs(build_candidates(nodes)) == {("n0", "n2"), ("n1", "n3")}
