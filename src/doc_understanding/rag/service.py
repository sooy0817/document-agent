# 역할: 검색 결과의 원본 복원·근거 검사·답변 생성을 연결한다.
"""Shared execution steps for the original CLI and LangGraph. No second retriever."""

from doc_understanding.rag import repository
from doc_understanding.rag.generation.answer import generate, render_answer
from doc_understanding.rag.evidence.pack import evidence_pack
from doc_understanding.rag.evidence.periods import interval_tables
from doc_understanding.rag.evidence.validation import validate_evidence as validate_evidence
from doc_understanding.runtime.telemetry import span


def resolve_evidence(hits, question, *, max_documents=3, max_cells_per_document=8, sources=None):
    """검색 결과의 근거를 복원한다. 문서 전체가 아니라 적재 때 저장한 근거 출처를 실행 ID로 읽는다.
    sources(실행 ID → 출처)를 주면 이미 읽은 출처를 함께 쓴다."""
    if max_documents < 1 or max_cells_per_document < 1:
        raise ValueError("INVALID_EVIDENCE_LIMIT")
    sources = {} if sources is None else sources
    evidence = []
    seen_docs = set()
    seen_runs = set()
    for hit in hits:
        rid = hit["run_id"]
        if rid in seen_runs:
            continue
        seen_runs.add(rid)
        if rid not in sources:
            sources[rid] = repository.evidence_source(rid)
        doc = sources[rid]
        if doc.document_id in seen_docs:
            continue
        seen_docs.add(doc.document_id)
        same = [h["payload"] for h in hits if h["run_id"] == rid]
        with span("table.resolve_cells", {"run_id": rid, "question": question}) as record:
            pack = evidence_pack(doc, same, question, max_cells=max_cells_per_document)
            for e in pack:
                e["evidence_id"] = f"E{len(evidence) + 1}"
                e["run_id"] = rid
                evidence.append(e)
            record["output"] = {"evidence": pack}
        if len(seen_docs) >= max_documents:
            break
    return evidence


def generate_answer(question, evidence, output, *, verify_sql=False, required_sources=(), **kwargs):
    from doc_understanding.rag.evidence.constraints import constrain_sources

    evidence, missing_sources = constrain_sources(evidence, required_sources)
    if missing_sources:
        selection = dict(evidence_ids=[], values_raw=[], abstained=True)
        return dict(
            question=question,
            answer=render_answer(selection, evidence),
            selection=selection,
            model_calls=0,
            facts_published=0,
            usage={},
            estimated_cost_usd=0,
            generation_mode="validation_abstention",
            reasons=["ORIGINAL_ENTITY_NOT_IN_EVIDENCE"],
            missing_sources=missing_sources,
        )
    if not evidence:
        return dict(
            question=question,
            answer="검색된 근거가 없습니다.",
            abstained=True,
            model_calls=0,
            facts_published=0,
        )
    panels = interval_tables(question, evidence)
    if panels:
        selection = dict(evidence_ids=[], values_raw=[], abstained=True)
        return dict(question=question, answer=render_answer(selection, []), selection=selection,
                    model_calls=0, facts_published=0, generation_mode="validation_abstention",
                    reasons=["PHYSICAL_INTERVAL_REQUIRED"], interval_tables=panels)
    validation = validate_evidence(evidence, question)
    if not validation["sufficient"]:
        return dict(
            question=question,
            answer=render_answer(dict(evidence_ids=[], values_raw=[], abstained=True), []),
            selection=dict(evidence_ids=[], values_raw=[], abstained=True),
            model_calls=0,
            generation_mode="validation_abstention",
            reasons=validation["reasons"],
            facts_published=0,
            usage={},
            estimated_cost_usd=0,
        )
    if verify_sql:
        checked = set()
        for e in evidence:
            context = e.get("nested_context")
            if not context:
                continue
            key = (e["run_id"], context["parent_cell_id"])
            if key in checked:
                continue
            rows = repository.sql_nested_probe(*key)
            if len(rows) != 1:
                raise ValueError("NESTED_SQL_PARENT_MISMATCH")
            contents = {c["content_id"]: c for c in rows[0]["contents"]}
            spans = set(rows[0]["parent_cell"]["span_refs"])
            for prose in context["prose"]:
                if contents.get(prose["content_id"], {}).get("text_raw") != prose[
                    "text_raw"
                ] or not set(prose["span_refs"]).issubset(spans):
                    raise ValueError("NESTED_SQL_PROSE_MISMATCH")
            checked.add(key)
    with span(
        "document.answer", {"question": question, "evidence": evidence}, generation=True
    ) as record:
        result = generate(question, evidence, output, **kwargs)
        record["output"] = {
            **result,
            "selected": [
                e for e in evidence if e["evidence_id"] in result["selection"]["evidence_ids"]
            ],
        }
    if verify_sql:
        ids = result["selection"]["evidence_ids"]
        for e in evidence:
            if e["evidence_id"] in ids and e.get("table_id"):
                rows = repository.sql_value_probe(e["run_id"], e["table_id"], e["cell_id"])
                if not rows or any(
                    rows[0][sql] != e[field]
                    for sql, field in (
                        ("value_raw", "value_raw"),
                        ("row_node_ids", "row_node_ids"),
                        ("column_node_ids", "column_node_ids"),
                    )
                ):
                    raise ValueError("SELECTED_SQL_CELL_MISMATCH")
    return result


def legacy(question, output, *, run_id=None, limit=8, **kwargs):
    hits = repository.search(question, run_id, limit)
    evidence = resolve_evidence(hits, question)
    return dict(
        original_question=question,
        candidates=hits,
        evidence=evidence,
        validation=validate_evidence(evidence, question),
        result=generate_answer(question, evidence, output, **kwargs),
    )
