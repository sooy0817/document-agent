import pytest
from pydantic import ValidationError
from doc_understanding.agents.semantic_resolver.models import SemanticPatch, SemanticPatchProposal
from doc_understanding.agents.semantic_resolver.validator import validate_patches
from doc_understanding.agents.semantic_resolver.candidates import build_candidates
from tests.test_semantic_resolver_rules import feature


def patch(p="n0", c="n1", e=("c0", "c1")):
    return SemanticPatch(
        parent_id=p, child_id=c, relation_type="parent_child", evidence_ids=list(e), reason="test"
    )


@pytest.mark.parametrize(
    "kind", ["unknown", "self", "panel", "reverse", "page", "evidence", "scope", "document"]
)
def test_bad_parent_blocked(kind):
    nodes = [feature(0, "I. A"), feature(1, "1. B")]
    p = patch()
    if kind == "unknown":
        p = patch("missing")
    if kind == "self":
        p = patch("n1", "n1", ("c1",))
    if kind == "panel":
        nodes[1] = nodes[1].model_copy(update={"panel_id": "other"})
    if kind == "reverse":
        p = patch("n1", "n0")
    if kind == "page":
        nodes[1] = nodes[1].model_copy(update={"page_number": 2})
    if kind == "evidence":
        p = patch(e=("invented",))
    if kind == "scope":
        nodes[1] = nodes[1].model_copy(update={"section_id": "other"})
    if kind == "document":
        nodes[1] = nodes[1].model_copy(
            update={
                "source_refs": (
                    nodes[1].source_refs[0].model_copy(update={"document_id": "other"}),
                )
            }
        )
    assert validate_patches(nodes, (), [p])[0].validation_status == "rejected"


def test_scope_boundary_blocks_stale_parent():
    nodes = [feature(i, t) for i, t in enumerate(["I. A", "1. B", "II. C", "1. D"])]
    d = validate_patches(nodes, (), [patch("n0", "n3", ("c0", "c3"))])[0]
    assert "NUMBERED_SCOPE_ENDED" in d.reasons and d.validation_status == "rejected"


def test_multiple_parents_remain_review_not_tree():
    nodes = [feature(i, t) for i, t in enumerate(["I. A", "(1) B", "1. C"])]
    ds = validate_patches(
        nodes,
        build_candidates(nodes),
        [patch("n0", "n2", ("c0", "c2")), patch("n1", "n2", ("c1", "c2"))],
    )
    assert all(
        d.validation_status == "review_required" and "MULTIPLE_PROPOSED_PARENTS" in d.reasons
        for d in ds
    )


def test_raw_numeric_text_fields_forbidden():
    with pytest.raises(ValidationError):
        SemanticPatchProposal.model_validate(
            {
                "patches": [
                    dict(
                        parent_id="n0",
                        child_id="n1",
                        relation_type="parent_child",
                        evidence_ids=["c0", "c1"],
                        reason="test",
                        value=123,
                    )
                ],
                "abstain_node_ids": [],
            }
        )


def test_schema_valid_is_not_semantically_verified():
    nodes = [feature(0, "I. A"), feature(1, "1. B")]
    ds = validate_patches(nodes, build_candidates(nodes), [patch()], method="text_llm")
    assert ds[0].validation_status == "review_required"


def test_model_can_select_review_candidate_but_cannot_promote_it():
    from uuid import uuid4
    from doc_understanding.agents.semantic_resolver.engine import assemble_nodes

    nodes = [feature(i, t) for i, t in enumerate(["I. A", "(1) B", "1. C"])]
    rules = validate_patches(
        nodes,
        build_candidates(nodes),
        [patch("n0", "n2", ("c0", "c2")), patch("n1", "n2", ("c1", "c2"))],
    )
    selection = validate_patches(
        nodes, build_candidates(nodes), [patch("n1", "n2", ("c1", "c2"))], "mock", rules
    )
    result = assemble_nodes(nodes, rules + selection, uuid4(), "mock")
    n = next(n for n in result if n.semantic_node_id == "n2")
    assert n.semantic_parent_id == "n1" and n.validation_status == "review_required"
    assert set(n.candidate_parent_ids) == {"n0", "n1"}


def test_cross_page_requires_every_boundary_not_one_bridge():
    from doc_understanding.agents.semantic_resolver.models import RelationCandidate

    nodes = [feature(0, "I. A"), feature(1, "1. B", page=2), feature(2, "2. C", page=3)]
    bridge = RelationCandidate(
        candidate_id="bridge",
        parent_id="n0",
        child_id="n1",
        relation_type="continuation",
        evidence_ids=("c0", "c1"),
        methods=("adjacent_pages",),
    )
    result = validate_patches(nodes, [bridge], [patch("n0", "n2", ("c0", "c2"))])[0]
    assert result.validation_status == "rejected" and "UNSUPPORTED_CROSS_PAGE" in result.reasons
