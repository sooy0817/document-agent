"""Provisional selected-source assertions, not exhaustive or human-certified gold."""

import json
import re
import unicodedata
from pathlib import Path
import pytest
from doc_understanding.domain.models import DocumentIR
from doc_understanding.parser.physical.physical_models import PhysicalTableIR
from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer
from doc_understanding.agents.semantic_resolver.engine import resolve_document
from doc_understanding.agents.semantic_resolver.models import ResolverConfig
from doc_understanding.agents.semantic_resolver.providers import MockTextAdapter

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data/semantic-c1/manifest.json"
GOLD = json.loads((ROOT / "tests/golden/semantic-c1-selected.json").read_text(encoding="utf8"))
pytestmark = pytest.mark.skipif(not MANIFEST.exists(), reason="Local real PDF artifacts absent")


def label(text):
    text = unicodedata.normalize("NFKC", text).strip()
    if not re.match(r"^\d{4}[./-]\d", text):
        text = re.sub(
            r"^(?:[IVXLCDM]+[.)]|\(\d+\)|[A-Za-z가-힣][.)]|\d+[.)\s]|[-•●]\s)\s*", "", text
        )
    return "".join(text.split())


@pytest.fixture(scope="module")
def corpus():
    output = {}
    for m in json.loads(MANIFEST.read_text(encoding="utf8")):
        run = Path(m["run"])
        d = DocumentIR.model_validate_json((run / "document-ir.json").read_bytes())
        physical = tuple(
            PhysicalTableIR.model_validate_json(p.read_bytes())
            for p in sorted((run / "physical").glob("page-*-table-*.json"))
        )
        structured = tuple(SemanticTableAnalyzer().analyze(t, physical) for t in physical)
        overlay, _ = resolve_document(
            d, physical, structured, accounting_scope=m["accounting_scope"]
        )
        output[m["slug"]] = (d, physical, structured, overlay)
    return output


@pytest.mark.parametrize("case", GOLD["paths"])
def test_selected_provisional_path(corpus, case):
    overlay = corpus[case["document"]][3]
    features = {f.node_id: f for f in overlay.features}
    matched = [
        n
        for n in overlay.table_nodes
        if [label(features[i].text_raw) for i in n.hierarchy_path] == case["labels"]
    ]
    assert matched
    if case.get("cross_page"):
        assert any(len({features[i].page_number for i in n.hierarchy_path}) > 1 for n in matched)


@pytest.mark.parametrize("case", GOLD["relations"])
def test_selected_provisional_relation(corpus, case):
    overlay = corpus[case["document"]][3]
    idx = {f.node_id: f for f in overlay.features}
    matches = [
        d
        for d in overlay.decisions
        if d.validation_status != "rejected"
        and d.proposal.relation_type == case["type"]
        and label(idx[d.proposal.parent_id].text_raw) == case["parent"]
        and label(idx[d.proposal.child_id].text_raw) == case["child"]
    ]
    assert matches
    if case.get("cross_page"):
        assert any(
            idx[d.proposal.parent_id].page_number != idx[d.proposal.child_id].page_number
            for d in matches
        )


def test_all_source_strings_numbers_boxes_unchanged(corpus):
    for _, physical, _, overlay in corpus.values():
        before = {
            c.cell_id: (c.original_text, c.source_bbox, c.status) for t in physical for c in t.cells
        }
        after = {c.cell_id: (c.text_raw, c.bbox, c.physical_status) for c in overlay.source_cells}
        assert before == after
    deficit = corpus["deficit"][3]
    assert any(c.text_raw == "" for c in deficit.source_cells)
    assert any(c.text_raw == "0" for c in deficit.source_cells)


def test_audit_routes_without_text_calls(corpus):
    doc, physical, structured, _ = corpus["audit"]
    overlay, requests = resolve_document(
        doc, physical, structured, "hybrid", MockTextAdapter(), ResolverConfig()
    )
    assert overlay.status == "requires_ocr_or_vision"
    assert not requests and not overlay.calls
    assert len(overlay.escalations) == 3


def test_business_prose_not_table_rows_and_meetings_isolated(corpus):
    o = corpus["business"][3]
    idx = {f.node_id: f for f in o.features}
    assert any("사업개황" in n.text_raw for n in o.features if n.axis == "document")
    assert all(idx[n.semantic_node_id].page_number >= 5 for n in o.table_nodes)
    assert any(d.proposal.relation_type == "continuation" for d in o.decisions)
    for n in o.table_nodes:
        date_labels = [
            label(idx[i].text_raw)
            for i in n.hierarchy_path
            if "date_anchor" in idx[i].role_candidates
        ]
        assert len(set(date_labels)) <= 1


def test_balance_panels_not_crossed(corpus):
    for slug in ["balance-credit", "balance-general", "balance-integrated"]:
        o = corpus[slug][3]
        idx = {f.node_id: f for f in o.features}
        assert len({f.panel_id.split("/")[-1] for f in o.features if f.axis == "row"}) == 2
        for d in o.decisions:
            if d.validation_status != "rejected":
                assert idx[d.proposal.parent_id].panel_id == idx[d.proposal.child_id].panel_id


# Contract hashes are checked without the corpus skip marker in test_frozen_contracts.py.
