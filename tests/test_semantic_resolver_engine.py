import pytest
from doc_understanding.domain.contracts import ExtractionRequest
from doc_understanding.parser.extraction.pymupdf_adapter import PyMuPDFExtractor
from doc_understanding.parser.orchestration.workflow import extract_ir
from doc_understanding.parser.physical.physical_grid import PhysicalGridReconstructor
from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer
from doc_understanding.agents.semantic_resolver.engine import resolve_document
from doc_understanding.agents.semantic_resolver.models import ResolverConfig
from doc_understanding.agents.semantic_resolver.providers import MockTextAdapter
from tests.semantic_fixtures import fixture_pdf


@pytest.mark.parametrize("kind", ["laboratory", "warehouse", "survey"])
def test_nonfinancial_pdf_pipeline_and_mock_abstention(kind, tmp_path):
    doc = extract_ir(
        ExtractionRequest(fixture_pdf(kind), "synthetic.pdf"), PyMuPDFExtractor(), tmp_path
    )
    physical = PhysicalGridReconstructor().analyze_page(doc.document_id, doc.pages[0]).tables
    structured = tuple(SemanticTableAnalyzer().analyze(t) for t in physical)
    before = [
        doc.model_dump_json(),
        *[t.model_dump_json() for t in physical],
        *[s.model_dump_json() for s in structured],
    ]
    config = ResolverConfig(
        max_nodes_per_request=8, max_input_tokens=20000, total_token_budget=50000
    )
    rule, _ = resolve_document(doc, physical, structured)
    hybrid, requests = resolve_document(
        doc, physical, structured, "hybrid", MockTextAdapter(), config
    )
    assert all(r.mode == "hybrid" for r in requests)
    assert {(n.semantic_node_id, n.semantic_parent_id) for n in rule.table_nodes} == {
        (n.semantic_node_id, n.semantic_parent_id) for n in hybrid.table_nodes
    }
    assert before == [
        doc.model_dump_json(),
        *[t.model_dump_json() for t in physical],
        *[s.model_dump_json() for s in structured],
    ]
    assert all(c.provider == "mock" for c in hybrid.calls)
    llm_only, reqs = resolve_document(
        doc, physical, structured, "llm_only", MockTextAdapter(), config
    )
    assert all(not r.candidates for r in reqs)
    assert all(n.semantic_parent_id is None for n in llm_only.table_nodes)
