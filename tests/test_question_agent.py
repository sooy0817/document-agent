"""질문 이해 에이전트(ADR-079): 계획 검사, 결정적 도구, 계획-실행 흐름. 외부 호출은 모의다."""
import json
from pathlib import Path

import pytest

from doc_understanding.rag.agentic.plan import QuestionPlan, make_plan, validate_plan
from doc_understanding.runtime.structured_call import CallPolicy

ROOT = Path(__file__).resolve().parents[1]
KB_DOCUMENT = ROOT / "work/document-explorer/331a24bbcd404065b06605ee777fc3b2/document.json"
POLICY = CallPolicy(model="test-model", input_usd_per_million=1.0, output_usd_per_million=2.0)
QUESTION = "확정급여형 에서 고정금리형 일 단위 계좌의 재예치기간은?"
PLAN = dict(requirements=[
    dict(question_quote="확정급여형", role="scope", text="확정급여형"),
    dict(question_quote="고정금리형 일 단위 계좌", role="item", text="고정금리형 일 단위 계좌"),
    dict(question_quote="재예치기간", role="item", text="재예치기간")],
    queries=["재예치기간 고정금리형 일 단위 계좌", "확정급여형 만기후처리"])


def scripted(*replies):
    queue = list(replies)

    def transport(payload, key, timeout):
        return dict(status="completed", usage={"input_tokens": 1, "output_tokens": 1},
                    output=[{"type": "message", "content": [
                        {"type": "output_text", "text": json.dumps(queue.pop(0), ensure_ascii=False)}]}])
    return transport


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-never-sent")


def reviewed_kb():
    if not KB_DOCUMENT.is_file():
        pytest.skip("local KB baseline document is not available")
    from doc_understanding.agents.structure_review.review import review_document
    from doc_understanding.domain.pipeline_contracts import CanonicalDocument
    return review_document(CanonicalDocument.model_validate_json(KB_DOCUMENT.read_bytes()))[0]


# --- 계획 계약과 인용 고정 ---


def test_plan_quotes_must_be_spans_of_the_question_ignoring_whitespace():
    assert validate_plan(QuestionPlan.model_validate(PLAN), QUESTION)["passed"]
    spaced = dict(PLAN, requirements=[dict(question_quote="고정금리형일단위계좌", role="item", text="x")])
    assert validate_plan(QuestionPlan.model_validate(spaced), QUESTION)["passed"]
    invented = dict(PLAN, requirements=[dict(question_quote="확정기여형", role="scope", text="확정기여형")])
    assert validate_plan(QuestionPlan.model_validate(invented), QUESTION)["reasons"] == [
        "REQUIREMENT_NOT_ANCHORED_IN_QUESTION"]


def test_valid_plan_is_used_and_unanchored_plan_falls_back(tmp_path, api_key):
    plan, check = make_plan(QUESTION, POLICY, tmp_path / "a", allow_external=True, transport=scripted(PLAN))
    assert not check["fallback"] and [r.role for r in plan.requirements] == ["scope", "item", "item"]
    bad = dict(PLAN, requirements=[dict(question_quote="없는 말", role="item", text="x")])
    plan, check = make_plan(QUESTION, POLICY, tmp_path / "b", allow_external=True, transport=scripted(bad))
    assert check["fallback"] and plan.queries == [QUESTION] and "rejected_plan" in check


def test_plan_call_failure_falls_back_without_raising(tmp_path, api_key):
    never = lambda *a: pytest.fail("no call")  # noqa: E731
    plan, check = make_plan(QUESTION, POLICY, tmp_path, allow_external=False, transport=never)
    assert check["fallback"] and check["reasons"] == ["PLAN_CALL_FAILED:EXTERNAL_CALL_NOT_ENABLED"]


# --- 결정적 도구 ---

def test_clarification_uses_a_question_span_and_only_asks_for_missing_scope(tmp_path, api_key):
    question = "보증 3개월이면 어떻게 되나요?"
    ambiguity = dict(question_quote="보증 3개월", readings=["보증기간 3개월", "종료 후 3개월"],
                     follow_up="보증기간이 3개월인가요, 종료 후 3개월이 지났나요?")
    plan = dict(queries=[question], requirements=[dict(question_quote="3개월", text="3개월", role="condition")],
                ambiguity=ambiguity)
    invalid = QuestionPlan.model_validate(dict(plan, ambiguity=dict(ambiguity, question_quote="종료 후")))
    assert not validate_plan(invalid, question)["passed"]
    from doc_understanding.rag.agentic.run import answer
    for count in (1, 2):
        run = answer(question, tmp_path / str(count), plan_policy=POLICY, plan_transport=scripted(plan),
                     allow_external=True, directory=lambda: [dict(name=f"문서{i}") for i in range(count)],
                     retriever=lambda *args: pytest.fail("clarification must precede retrieval"))
        result = run["result"]
        assert result["clarification"]["kind"] == "needs_context" and result["selection"]["abstained"]
        assert ("어느 문서" in result["answer_text"]) == (count > 1)
        assert ambiguity["follow_up"] in result["answer_text"] and not run["attempts"]

from doc_understanding.rag.agentic.tools import structure_matches, table_outline  # noqa: E402


def _table(doc, suffix):
    return next(t.table_id for t in doc.table_structure if t.table_id.endswith(suffix))


def test_structure_match_finds_cell_by_row_and_column_labels_and_scope():
    doc = reviewed_kb()
    top = structure_matches(doc, _table(doc, "p0006/physical001"),
                            ["고정금리형 일 단위 계좌", "재예치기간"], ["확정급여형"])[0]
    assert top["value_raw"] == "12개월" and top["match_score"] == 3 and top["scope_matched"] is True


def test_scope_mismatch_lowers_score_but_keeps_candidate():
    doc = reviewed_kb()
    top = structure_matches(doc, _table(doc, "p0006/physical001"),
                            ["고정금리형 일 단위 계좌", "재예치기간"], ["확정기여형"])[0]
    assert top["scope_matched"] is False and top["match_score"] == 1


def test_unresolved_cells_are_never_returned_and_outline_reports_scope():
    from doc_understanding.domain.pipeline_contracts import CanonicalDocument
    reviewed_kb()  # 로컬 기준 문서가 없으면 건너뛴다.
    original = CanonicalDocument.model_validate_json(KB_DOCUMENT.read_bytes())
    # 검토 전 원본에서는 6쪽 값이 미확정이므로 구조 일치 결과가 없다.
    assert structure_matches(original, _table(original, "p0006/physical001"), ["재예치기간"]) == []
    outline = table_outline(reviewed_kb(), _table(original, "p0003/physical001"))
    assert outline["scope"] == ["[변동금리형]"] and outline["eligible"] > 0


# --- 계획-실행 흐름 ---

from doc_understanding.rag import repository  # noqa: E402
from doc_understanding.rag.agentic.run import answer, merge_hits  # noqa: E402


def test_merge_hits_interleaves_queries_and_deduplicates():
    a = [dict(run_id="r", payload={"entry_id": x}) for x in ("1", "2", "3")]
    b = [dict(run_id="r", payload={"entry_id": x}) for x in ("2", "4")]
    assert [h["payload"]["entry_id"] for h in merge_hits([a, b])] == ["1", "2", "4", "3"]


@pytest.fixture
def kb_runtime(monkeypatch):
    from doc_understanding.rag.indexing.entries import make_entries
    doc = reviewed_kb()
    monkeypatch.setattr(repository, "evidence_source", lambda rid: doc)
    entries = make_entries(doc)
    # 검색이 다른 쪽 표를 먼저 돌려주는 상황: 1쪽 행 항목 4개 뒤에 6쪽 안쪽 표 행 항목.
    noisy = [e for e in entries if e["page"] == 1 and e["kind"] == "table_row"][:4]
    target = [e for e in entries if e.get("table_id", "") and e["table_id"].endswith("p0006/physical001")
              and e["kind"] == "table_row"]
    queries = []

    def retriever(q, rid, n):
        queries.append(q)
        return [dict(run_id="run-1", payload=e, score=1.0) for e in noisy + target]
    return doc, retriever, queries


def test_structured_match_puts_target_cell_first_and_keeps_answer_checks(tmp_path, api_key, kb_runtime):
    _, retriever, queries = kb_runtime
    seen = {}

    def generate(question, evidence, output, **kwargs):
        seen.update(evidence=evidence, kwargs=kwargs)
        return dict(answer="ok", model_calls=1, assessment_validation=dict(
            passed=True, assessment=dict(requirements=[dict(question_quote="재예치기간", status="supported")])))

    report = answer(QUESTION, tmp_path / "run", run_id="run-1", retriever=retriever, plan_policy=POLICY,
                    allow_external=True, plan_transport=scripted(PLAN), generate=generate)
    first = seen["evidence"][0]
    assert first["evidence_id"] == "E1" and first["value_raw"] == "12개월" and first["page"] == 6
    assert first["scope_context"][0]["text_raw"] == "[확정급여형(DB)]" and first["run_id"] == "run-1"
    assert len({(e.get("cell_id"), e.get("content_id")) for e in seen["evidence"]}) == len(seen["evidence"])
    assert queries[:2] == PLAN["queries"] and QUESTION in queries
    assert seen["kwargs"]["verify_sql"] is True and not report["retried"]
    assert report["plan_model_calls"] == 1 and report["answer_model_calls"] == 1
    assert (tmp_path / "run/agent-run.json").is_file() and (tmp_path / "run/plan/request.json").is_file()


def test_unsupported_requirement_does_not_answer_again_with_the_same_evidence(tmp_path, api_key, kb_runtime):
    _, retriever, queries = kb_runtime
    calls = []

    def generate(question, evidence, output, **kwargs):
        # 두 번째 답변도 근거 부족이지만 재검색은 한 번뿐이어야 한다.
        calls.append(output.name)
        return dict(answer="보류", model_calls=1, abstained=True, assessment_validation=dict(
            passed=False, assessment=dict(requirements=[dict(question_quote="확정급여형", status="unsupported")])))

    report = answer(QUESTION, tmp_path / "run", run_id="run-1", retriever=retriever, plan_policy=POLICY,
                    allow_external=True, plan_transport=scripted(PLAN), generate=generate)
    assert calls == ["answer"] and report["retried"]
    assert report["attempts"][1]["retry_quotes"] == ["확정급여형"] and "확정급여형" in queries
    assert report['attempts'][1]['trace']['queries']==['확정급여형']
    assert report['attempts'][1]['trace']['retry']['reason']=='NO_NEW_RETRIEVED_EVIDENCE'


def test_without_planner_the_question_itself_is_searched(tmp_path, kb_runtime):
    _, retriever, queries = kb_runtime
    report = answer(QUESTION, tmp_path / "run", run_id="run-1", retriever=retriever,
                    generate=lambda q, e, o, **k: dict(answer="x", model_calls=0))
    assert report["plan_check"]["reasons"] == ["PLANNER_DISABLED"] and set(queries) == {QUESTION}
    assert report["plan_model_calls"] == 0


# --- 실제 실행에서 발견한 결함 회귀 (2026-11-10 gpt-4.1 실행) ---


def test_column_label_misclassified_as_scope_still_drives_structure_match(tmp_path, api_key, kb_runtime):
    _, retriever, _ = kb_runtime
    live_plan = dict(requirements=[
        dict(question_quote="확정급여형", role="scope", text="확정급여형"),
        dict(question_quote="고정금리형", role="scope", text="고정금리형"),
        dict(question_quote="일 단위 계좌", role="scope", text="일 단위 계좌"),
        dict(question_quote="재예치기간", role="item", text="재예치기간")], queries=["재예치기간"])
    seen = {}
    report = answer(QUESTION, tmp_path / "run", run_id="run-1", retriever=retriever, plan_policy=POLICY,
                    allow_external=True, plan_transport=scripted(live_plan),
                    generate=lambda q, e, o, **k: seen.update(evidence=e) or dict(answer="x", model_calls=1))
    top = report["attempts"][0]["trace"]["structured"][0]
    assert top["value_raw"] == "12개월" and {"고정금리형", "일 단위 계좌", "재예치기간"} <= set(top["matched_terms"])
    assert top["match_score"] > report["attempts"][0]["trace"]["structured"][1]["match_score"]


def test_scope_heading_is_an_accepted_condition_source():
    from doc_understanding.rag.generation.narrative import build_catalog, validate_render
    doc = reviewed_kb()
    from doc_understanding.rag.evidence.cells import cells_for_table
    cell = next(c for c in cells_for_table(doc, _table(doc, "p0006/physical001")) if c["value_raw"] == "12개월")
    cell.update(evidence_id="E1", run_id="r")
    catalog = build_catalog([cell])
    scope_id = next(s["source_id"] for s in catalog if s["kind"] == "scope_context")
    draft = dict(
        assessment=dict(question=QUESTION, status="supported", requires_external_verification=False,
                        requirements=[dict(question_quote="재예치기간", requirement="r", status="supported",
                                           supports=[dict(source_id="E1", field="text_raw", quote="12개월")])],
                        explanation=""),
        target_term="재예치기간", abstained=False, reason="",
        sentences=[dict(text="고정금리형 일 단위 계좌의 재예치기간은 12개월입니다.", kind="value",
                        citations=[dict(source_id="E1", quote="12개월")])],
        condition_assessments=[dict(source_id=scope_id, applicability="direct", sentence=dict(
            text="[확정급여형(DB)]", kind="condition", citations=[dict(source_id=scope_id, quote="[확정급여형(DB)]")]))])
    result = validate_render(draft, catalog, QUESTION, [cell])
    assert "INVALID_CONDITION_SOURCE" not in result["sentence_validation"]["reasons"]
    assert result["sentence_validation"]["passed"], result["sentence_validation"]["reasons"]


# --- Langfuse 기록: 모든 모델 호출과 단계가 하나의 trace에 남는지 (가짜 Trace) ---

from contextlib import contextmanager  # noqa: E402

from doc_understanding.runtime import telemetry  # noqa: E402


class FakeTrace:
    def __init__(self):
        self.spans, self.trace_id, self.warnings = [], "trace-1", []

    @contextmanager
    def span(self, name, input=None, *, generation=False):
        box = {}
        yield box
        self.spans.append(dict(name=name, generation=generation, input=input, output=box.get("output")))

    @contextmanager
    def request(self, question, mode):
        token = telemetry.ACTIVE.set(self)
        try:
            with self.span("nh-table-rag", dict(question=question, mode=mode)) as box:
                yield box
        finally:
            telemetry.ACTIVE.reset(token)

    def flush(self):
        pass


def test_agent_run_records_plan_search_match_and_model_generations(tmp_path, api_key, kb_runtime):
    _, retriever, _ = kb_runtime
    from doc_understanding.rag.embedding.client import EmbeddingClient
    from doc_understanding.rag.embedding.models import EmbeddingConfig
    trace = FakeTrace()
    embedder = EmbeddingClient(EmbeddingConfig(dimensions=3, input_usd_per_million=0.02), allow_external=True,
                               transport=lambda p, k, t: dict(model=p["model"], usage={"prompt_tokens": 3},
                                                              data=[{"index": 0, "embedding": [1.0, 0, 0]}]))

    def embedding_retriever(q, rid, n):
        embedder.embed([q])
        return retriever(q, rid, n)

    report = answer(QUESTION, tmp_path / "run", trace=trace, run_id="run-1", retriever=embedding_retriever,
                    plan_policy=POLICY, allow_external=True, plan_transport=scripted(PLAN),
                    generate=lambda q, e, o, **k: dict(answer="ok", model_calls=0))
    names = [s["name"] for s in trace.spans]
    assert "model.question_plan" in names and "agent.plan" in names and "agent.structure_match" in names
    assert names.count("agent.search") == len(PLAN["queries"]) + 1
    assert names.count("model.embedding") == len(PLAN["queries"]) + 1 and names[-1] == "nh-table-rag"
    plan_call = next(s for s in trace.spans if s["name"] == "model.question_plan")
    assert plan_call["generation"] and plan_call["output"]["usage"]["input_tokens"] == 1
    emb = next(s for s in trace.spans if s["name"] == "model.embedding")
    assert emb["generation"] and emb["output"]["usage"]["input_tokens"] == 3 and "input_hashes" in emb["input"]
    assert report["trace_id"] == "trace-1" and (tmp_path / "run/trace.json").is_file()


def test_redaction_keeps_new_agent_fields_but_blocks_document_text():
    kept = telemetry.redact(dict(matched_terms=["a"], match_score=2, plan_check={"fallback": False},
                                 text="원문 전체", primary_text="x", payload={"text": "x"}))
    assert kept == dict(matched_terms=["a"], match_score=2, plan_check={"fallback": False})


def test_context_source_in_selection_is_dropped_only_with_its_carrier():
    from doc_understanding.rag.generation.answer import drop_context_selections
    evidence = [dict(evidence_id="E1", value_raw="0.10")]
    catalog = [dict(source_id="E1", carrier_evidence_id="E1", kind="table_cell", text_raw="0.10"),
               dict(source_id="S1", carrier_evidence_id="E1", kind="scope_context", text_raw="[변동금리형]")]
    kept, dropped = drop_context_selections(
        dict(evidence_ids=["E1", "S1"], values_raw=["0.10", "[변동금리형]"], abstained=False), catalog, evidence)
    assert kept["evidence_ids"] == ["E1"] and kept["values_raw"] == ["0.10"]
    assert dropped == [dict(source_id="S1", kind="scope_context", carrier_evidence_id="E1")]
    # carrier가 선택되지 않았거나 값이 다르면 그대로 두어 기존 검사가 거부하게 한다.
    alone, none = drop_context_selections(dict(evidence_ids=["S1"], values_raw=["[변동금리형]"], abstained=False),
                                          catalog, evidence)
    assert alone["evidence_ids"] == ["S1"] and none == []
    changed, none = drop_context_selections(dict(evidence_ids=["E1", "S1"], values_raw=["0.10", "다른 값"],
                                                 abstained=False), catalog, evidence)
    assert changed["evidence_ids"] == ["E1", "S1"] and none == []


def test_context_with_separate_carrier_needs_its_exact_assessment_quote():
    from doc_understanding.rag.generation.answer import drop_context_selections
    evidence = [dict(evidence_id="E1"), dict(evidence_id="E2")]
    catalog = [dict(source_id="P2", carrier_evidence_id="E2", kind="parent_text", text_raw="limit")]
    selection = dict(evidence_ids=["E1", "P2"], values_raw=["value", "limit"], abstained=False)
    support = dict(source_id="P2", field="text_raw", quote="limit")
    assessment = dict(requirements=[dict(supports=[support])])
    kept, dropped = drop_context_selections(selection, catalog, evidence, assessment)
    assert kept["evidence_ids"] == ["E1"] and dropped[0]["carrier_evidence_id"] == "E2"
    support["quote"] = "different"
    assert drop_context_selections(selection, catalog, evidence, assessment) == (selection, [])


def test_plan_cache_reuses_a_validated_plan_and_skips_the_model(tmp_path):
    from doc_understanding.rag.agentic.plan import PlanCache
    from doc_understanding.runtime.structured_call import CallPolicy
    policy = CallPolicy(model="plan-model", input_usd_per_million=1, output_usd_per_million=1)
    question = "A 상품의 금리는?"
    first = QuestionPlan(requirements=[dict(question_quote="A 상품의 금리", role="item", text="A 상품의 금리")],
                         queries=["A 상품 금리"])
    cache = PlanCache(tmp_path / "plans")
    assert cache.get(question, "plan-model") is None
    cache.put(question, "plan-model", first)

    def never(*_):
        raise AssertionError("cached plan must not call the model")

    plan, check = make_plan(question, policy, tmp_path / "out", allow_external=True, transport=never, cache=cache)
    assert plan == first and check["cached"] and not check["fallback"]
    # 모델이나 질문 문구가 다르면 다른 계획이다.
    assert cache.get(question, "other-model") is None and cache.get(question + " ", "plan-model") is None
    # 깨진 보관 파일은 없는 것으로 본다.
    cache.path(question, "plan-model").write_text("{", encoding="utf8")
    assert cache.get(question, "plan-model") is None


def test_answer_request_fixes_sampling_except_for_reasoning_models(tmp_path, monkeypatch):
    from doc_understanding.rag.generation import answer as module
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    seen = []

    def transport(payload, key, timeout):
        seen.append(payload)
        raise RuntimeError("stop after capturing the request")

    evidence = [dict(evidence_id="E1", source_name="a.pdf", page=1, table_id=None, cell_id=None, content_id="c1",
                     value_raw="본문", value_kind="body", lookup_eligible=True)]
    for model in ("gpt-4.1", "o3-mini"):
        try:
            module.generate("질문", evidence, tmp_path / model, allow_external=True, transport=transport, model=model,
                            input_usd_per_million=1, output_usd_per_million=1)
        except Exception:
            pass
    assert seen[0]["temperature"] == 0.0 and "temperature" not in seen[1]


# --- 읽기 범위: 표 전체를 읽고 답하기 ---


def test_whole_reading_plan_gives_the_answer_model_every_cell_of_the_matched_table(tmp_path, api_key, kb_runtime):
    _, retriever, _ = kb_runtime
    seen, stages = {}, []

    def generate(question, evidence, output, **kwargs):
        seen.setdefault(output.name, evidence)
        return dict(answer="ok", model_calls=1)

    focused = answer(QUESTION, tmp_path / "focused", run_id="run-1", retriever=retriever, plan_policy=POLICY,
                     allow_external=True, plan_transport=scripted(PLAN), generate=generate)
    narrow = seen.pop("answer")
    report = answer(QUESTION, tmp_path / "whole", run_id="run-1", retriever=retriever, plan_policy=POLICY,
                    allow_external=True, plan_transport=scripted(dict(PLAN, reading="whole")), generate=generate,
                    progress=stages.append)
    wide = seen["answer"]
    record = report["attempts"][0]["trace"]["reading"]
    assert "reading" not in focused["attempts"][0]["trace"] and report["plan"]["reading"] == "whole"
    assert len(wide) > len(narrow) and record["after"] == len(wide) and record["before"] == len(narrow)
    # 넓히기 전 근거는 하나도 빠지지 않고, 번호는 E1부터 다시 매긴다.
    key = lambda e: e.get("cell_id") or e.get("content_id") or e.get("chunk_id")  # noqa: E731
    assert {key(e) for e in narrow} <= {key(e) for e in wide}
    assert [e["evidence_id"] for e in wide] == [f"E{i}" for i in range(1, len(wide) + 1)]
    # 표 하나를 넓히면 그 표의 조회 가능한 칸이 전부 들어온다.
    table = next(t for t in record["tables"] if t["expanded"])
    assert sum(1 for e in wide if e.get("table_id") == table["table_id"]) == table["cells"]
    assert stages.index("reading") < stages.index("answering") and report["evidence"] == wide


def test_abstention_reads_the_whole_table_once_and_answers_again(tmp_path, api_key, kb_runtime):
    _, retriever, _ = kb_runtime
    calls = []

    def generate(question, evidence, output, **kwargs):
        calls.append((output.name, len(evidence)))
        return dict(answer="보류", model_calls=1, selection=dict(evidence_ids=[], values_raw=[], abstained=True),
                    assessment_validation=dict(assessment=dict(requirements=[dict(question_quote=QUESTION,status='unsupported')])))

    report = answer(QUESTION, tmp_path / "run", run_id="run-1", retriever=retriever, plan_policy=POLICY,
                    allow_external=True, plan_transport=scripted(PLAN), generate=generate)
    assert [name for name, _ in calls] == ["answer", "answer-whole"] and calls[1][1] > calls[0][1]
    assert report["answer_model_calls"] == 2 and not report["retried"]
    assert report["attempts"][0]["trace"]["reading"]["after_abstention"] is True


def test_whole_table_over_the_request_limit_falls_back_to_the_selected_evidence(tmp_path, api_key, kb_runtime):
    _, retriever, _ = kb_runtime
    calls = []

    def generate(question, evidence, output, **kwargs):
        calls.append((output.name, len(evidence)))
        if len(calls) == 1:
            raise ValueError("MODEL_REQUEST_BUDGET_EXCEEDED")
        return dict(answer="ok", model_calls=1)

    report = answer(QUESTION, tmp_path / "run", run_id="run-1", retriever=retriever, plan_policy=POLICY,
                    allow_external=True, plan_transport=scripted(dict(PLAN, reading="whole")), generate=generate)
    assert [name for name, _ in calls] == ["answer", "answer-focused"] and calls[1][1] < calls[0][1]
    record = report["attempts"][0]["trace"]["reading"]
    assert record["used"] is False and record["reason"] == "REQUEST_TOO_LARGE" and len(report["evidence"]) == calls[1][1]
    # 기록의 근거 번호는 실제로 쓴 근거와 같고, 전체를 읽지 못했다는 표시가 결과에 남는다.
    assert [i for i, _, _ in report["attempts"][0]["trace"]["evidence_ids"]] == [e["evidence_id"] for e in report["evidence"]]
    assert report["result"]["reading_incomplete"]["reason"] == "REQUEST_TOO_LARGE"


def test_evidence_used_in_the_assessment_is_added_to_the_citations():
    from doc_understanding.rag.generation.answer import include_assessed
    evidence = [dict(evidence_id="E1", value_raw="거래 제한", lookup_eligible=True, row_path=["거래중지"], column_path=["내용"]),
                dict(evidence_id="E2", value_raw="상품명 : A통장", lookup_eligible=True, row_path=["상품명"], column_path=["내용"]),
                dict(evidence_id="E3", value_raw="미확정", lookup_eligible=False)]
    catalog = [dict(source_id="E1", carrier_evidence_id="E1"), dict(source_id="E2", carrier_evidence_id="E2"),
               dict(source_id="H1", carrier_evidence_id="E2"), dict(source_id="E3", carrier_evidence_id="E3")]
    assessment = dict(requirements=[dict(supports=[dict(source_id="H1"), dict(source_id="E1"), dict(source_id="E3"),
                                                    dict(source_id="없는 출처")])])
    chosen = dict(evidence_ids=["E1"], values_raw=["거래 제한"], abstained=False)
    selection, added = include_assessed(chosen, assessment, catalog, evidence)
    # 판단에 쓴 문맥 출처(H1)의 근거 E2가 인용에 더해진다. 조회 불가 근거와 모르는 출처는 더하지 않는다.
    assert selection["evidence_ids"] == ["E1", "E2"] and selection["values_raw"] == ["거래 제한", "상품명 : A통장"]
    assert added == ["E2"] and chosen["evidence_ids"] == ["E1"]
    # 보류는 답으로 바뀌지 않는다.
    held = dict(evidence_ids=[], values_raw=[], abstained=True)
    assert include_assessed(held, assessment, catalog, evidence) == (held, [])


def test_citations_added_from_the_assessment_do_not_have_to_appear_in_the_answer_sentence():
    from types import SimpleNamespace
    from doc_understanding.rag.generation.answer import present
    evidence = [dict(evidence_id="E1", value_raw="2.85", row_path=["12개월 이상"], column_path=["기본금리"]),
                dict(evidence_id="E2", value_raw="상품명 : A 정기예금", row_path=["상품명"], column_path=["내용"])]
    parsed = SimpleNamespace(answer_text="A 정기예금의 기본금리는 2.85입니다.", alternatives=[])
    selection = dict(evidence_ids=["E1", "E2"], values_raw=["2.85", "상품명 : A 정기예금"], abstained=False)
    # 판단 근거로 더한 E2의 원문 전체가 문장에 없어도 모델의 문장을 그대로 쓴다.
    kept = present(parsed, selection, evidence, "A 정기예금 12개월 금리", required_values=["2.85"])
    assert kept["answer_text_source"] == "model" and kept["answer_text"] == parsed.answer_text
    # 모델이 직접 고른 값이 문장에 없으면 예전처럼 템플릿 문장으로 바꾼다.
    assert present(parsed, selection, evidence, "A 정기예금 12개월 금리")["answer_text_source"] == "template"


def test_answer_request_names_each_source_document_once(tmp_path, monkeypatch):
    # 올린 문서는 파일 이름이 모두 input.pdf라, 문서 이름표 없이는 모델이 어느 기관·상품의 값인지 알 수 없다.
    from doc_understanding.rag.generation import answer as module
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    seen = {}

    def transport(payload, key, timeout):
        seen.update(payload=payload, key=key)
        raise RuntimeError("stop after capturing the request")

    def item(number, document, label):
        return dict(evidence_id=f"E{number}", document_id=document, source_name="input.pdf", document_label=label, page=1,
                    table_id="t", cell_id=f"c{number}", value_raw=str(number), value_kind="number", row_path=["금리"],
                    column_path=["12개월"], lookup_eligible=True)

    evidence = [item(1, "doc-a", "input.pdf | A은행 정기예금 상품설명서"), item(2, "doc-b", "input.pdf | B농협 현황"),
                item(3, "doc-a", "input.pdf | A은행 정기예금 상품설명서")]
    try:
        module.generate("A은행 정기예금 금리", evidence, tmp_path / "out", allow_external=True, transport=transport,
                        model="gpt-4.1", input_usd_per_million=1, output_usd_per_million=1)
    except RuntimeError:
        pass
    sent = json.loads(seen["payload"]["input"][1]["content"])
    assert sent["documents"] == {"D1": "input.pdf | A은행 정기예금 상품설명서", "D2": "input.pdf | B농협 현황"}
    assert [s["doc"] for s in sent["sources"] if s["source_id"].startswith("E")] == ["D1", "D2", "D1"]
    assert seen["key"] == "test"  # 요청을 만드는 코드가 API 키 변수를 건드리지 않는다


# --- 문서 고르기: 질문의 대상을 문서와 맞춰 검색 범위를 정한다 ---

SUBJECT_QUESTION = "가람은행 " + QUESTION


def catalog(*rows):
    return [dict(run_id=r, document_id="doc-" + r, name="input.pdf", source_name="input.pdf", pages=8, organization=o,
                 title=t, document_type="", as_of="", aliases=[]) for r, o, t in rows]


def subject_plan(*subjects):
    return scripted(dict(PLAN, subjects=list(subjects)))


@pytest.mark.parametrize("cite_both", [True, False])
def test_comparison_searches_each_run_and_requires_both_citations(tmp_path, api_key, monkeypatch, cite_both):
    from doc_understanding.rag.agentic import run as module
    from doc_understanding.rag.agentic.rerank import NoReranker

    question = "가람과 나래의 값은 각각?"
    plan = dict(requirements=[dict(question_quote="값", role="item", text="값")],
                queries=["값"], subjects=["가람", "나래"])
    searched = []

    def gather(question, plan, rid, retriever, **kwargs):
        assert kwargs["whole_document"] == rid and retriever == [rid]
        searched.append(rid)
        evidence = [dict(evidence_id=f"E{i}", run_id=rid, content_id=f"{rid}-{i}", value_raw="같은 값")
                    for i in range(1, 5)]
        return evidence, dict(queries=["값"], hits=[], tables=[], structured=[])

    def generate(question, evidence, output, **kwargs):
        assert [(e["evidence_id"], e["run_id"]) for e in evidence] == [("E1", "a"), ("E2", "b")]
        return dict(answer="같은 값", answer_text="같은 값", model_calls=1,
                    selection=dict(evidence_ids=["E1", "E2"] if cite_both else ["E1"], abstained=False))

    monkeypatch.setattr(module, "gather", gather)
    report = answer(question, tmp_path / "run", plan_policy=POLICY, allow_external=True, plan_transport=scripted(plan),
                    directory=lambda: catalog(("a", "가람", "설명"), ("b", "나래", "안내")), narrow=list,
                    explicit_scope=True, reranker=NoReranker(), max_evidence=2, generate=generate)
    result = report["result"]
    assert result["selection"]["abstained"] is (not cite_both)
    assert result["subject_check"] == dict(passed=cite_both, missing=[] if cite_both else ["나래"])
    assert searched == (["a", "b"] if cite_both else ["a", "b", "a", "b"])
    if not cite_both:
        assert result["answer_text"] == "" and result["reasons"] == ["MISSING_COMPARISON_CITATION"]


@pytest.mark.parametrize("named_row", [False, True])
def test_grid_fallback_cannot_answer_a_comparison_from_one_document(tmp_path, api_key, monkeypatch, named_row):
    from doc_understanding.rag.agentic import run as module

    question = "가람과 나래의 처리시간은 각각?"
    plan = dict(requirements=[dict(question_quote="처리시간", role="item", text="처리시간")],
                queries=["처리시간"], subjects=["가람", "나래"])

    def no_eligible_cells(question, plan, rid, retriever, **kwargs):
        table = dict(run_id=rid, table_id=rid + "-table", values=1, eligible=0)
        if named_row:
            table["requested_row"] = "처리시간"
        return [], dict(queries=["값"], hits=[], tables=[table], structured=[])

    def read_tables(question, tables, *args, **kwargs):
        assert tables == [("a", "a-table"), ("b", "b-table")]
        if named_row:
            assert kwargs["required_rows"] == {"a-table": "처리시간", "b-table": "처리시간"}
        return (dict(status="answered", answer_text="같은 값"),
                [dict(run_id="a", evidence_id="E1", value_raw="같은 값")], dict(tables=[]))

    monkeypatch.setattr(module, "gather", no_eligible_cells)
    monkeypatch.setattr(module.grid_reading, "read_tables", read_tables)
    report = answer(question, tmp_path / "run", plan_policy=POLICY, allow_external=True, plan_transport=scripted(plan),
                    directory=lambda: catalog(("a", "가람", "설명"), ("b", "나래", "안내")), narrow=list,
                    grid_loader=lambda *args: None, grid_policy=POLICY)
    assert report["result"]["abstained"] and report["evidence"] == []
    assert report["attempts"][0]["trace"]["grid"]["reasons"] == ["MISSING_COMPARISON_CITATION"]


def test_a_matched_subject_narrows_the_search_to_that_document(tmp_path, api_key, kb_runtime, monkeypatch):
    _, retriever, _ = kb_runtime
    narrowed, stages, listed = [], [], []
    # 범위가 문서 하나로 정해지면 그 문서의 표 목록을 읽어, 검색에 걸리지 않은 표도 구조 일치 후보에 넣는다.
    monkeypatch.setattr(repository, "table_ids", lambda rid: listed.append(rid) or
                        [t.table_id for t in kb_runtime[0].table_structure][:3])

    def narrow(run_ids):
        narrowed.append(list(run_ids))
        return retriever

    report = answer(SUBJECT_QUESTION, tmp_path / "run", run_id="run-1", retriever=retriever, plan_policy=POLICY,
                    allow_external=True, plan_transport=subject_plan("가람은행"), progress=stages.append,
                    generate=lambda q, e, o, **k: dict(answer="ok", model_calls=1),
                    directory=lambda: catalog(("run-1", "가람은행", "퇴직연금 정기예금"), ("run-2", "나래농협", "현황")),
                    narrow=narrow, mentions=lambda word, run_id=None: False)
    assert narrowed == [["run-1"]] and report["documents"]["status"] == "selected"
    assert report["documents"]["run_ids"] == ["run-1"] and "subject_check" not in report["result"]
    assert stages[:3] == ["planning", "selecting", "searching"] and listed == ["run-1"]
    seen = {t["table_id"] for t in report["attempts"][0]["trace"]["tables"]}
    assert {t.table_id for t in kb_runtime[0].table_structure[:3]} <= seen


def test_several_equal_matches_ask_back_without_searching_or_answering(tmp_path, api_key, kb_runtime):
    _, retriever, queries = kb_runtime

    def never(*args, **kwargs):
        raise AssertionError("no answer call when asking back")

    report = answer(SUBJECT_QUESTION, tmp_path / "run", run_id="run-1", retriever=retriever, plan_policy=POLICY,
                    allow_external=True, plan_transport=subject_plan("가람은행"), generate=never,
                    directory=lambda: catalog(("run-1", "가람은행", "퇴직연금 정기예금"), ("run-2", "가람은행", "자유적금")),
                    narrow=never, mentions=lambda word, run_id=None: False)
    ask = report["result"]["clarification"]
    assert ask["kind"] == "needs_choice" and [d["run_id"] for d in ask["documents"]] == ["run-1", "run-2"]
    assert ask["subject"] == "가람은행" and report["result"]["selection"]["abstained"]
    assert queries == [] and report["attempts"] == [] and report["evidence"] == [] and report["answer_model_calls"] == 0


def test_a_subject_no_document_mentions_is_answered_as_missing(tmp_path, api_key, kb_runtime):
    _, retriever, queries = kb_runtime
    report = answer(SUBJECT_QUESTION, tmp_path / "run", run_id="run-1", retriever=retriever, plan_policy=POLICY,
                    allow_external=True, plan_transport=subject_plan("가람은행"),
                    generate=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no answer call")),
                    directory=lambda: catalog(("run-1", "나래농협", "현황")), mentions=lambda word, run_id=None: False)
    assert report["result"]["clarification"]["kind"] == "not_found" and queries == []
    assert report["documents"]["subjects"][0]["missing"] == ["가람은행"]


def test_unconfirmed_subject_blocks_an_answer_cited_from_a_document_that_does_not_mention_it(tmp_path, api_key, kb_runtime):
    _, retriever, _ = kb_runtime
    said = dict(word=True)

    def generate(question, evidence, output, **kwargs):
        return dict(answer="12개월", answer_text="12개월입니다.", model_calls=1,
                    selection=dict(evidence_ids=[evidence[0]["evidence_id"]], values_raw=["12개월"], abstained=False))

    def run(folder):
        # 이름표에는 없고 본문 어딘가에만 나오는 대상이라 좁히지 못한다. 전체에서 찾고, 답한 뒤 인용 문서를 확인한다.
        return answer(SUBJECT_QUESTION, tmp_path / folder, run_id="run-1", retriever=retriever, plan_policy=POLICY,
                      allow_external=True, plan_transport=subject_plan("가람은행"), generate=generate,
                      directory=lambda: catalog(("run-1", "나래농협", "현황")),
                      mentions=lambda word, run_id=None: True if run_id is None else said["word"])

    passed = run("passed")
    assert passed["documents"]["status"] == "unconfirmed" and passed["result"]["subject_check"] == dict(passed=True, missing=[])
    assert not passed["result"]["selection"]["abstained"]
    said["word"] = False   # 인용한 문서 본문에 그 대상이 없다 → 다른 대상의 문서에서 가져온 값이다
    blocked = run("blocked")
    assert blocked["result"]["selection"] == dict(evidence_ids=[], values_raw=[], abstained=True)
    assert blocked["result"]["subject_check"] == dict(passed=False, missing=["가람은행"])
    assert blocked["result"]["reasons"] == ["CITED_DOCUMENT_DOES_NOT_MENTION_SUBJECT"]


def test_subjects_not_in_the_question_and_missing_directory_do_not_select(tmp_path, api_key, kb_runtime):
    _, retriever, _ = kb_runtime
    common = dict(run_id="run-1", retriever=retriever, plan_policy=POLICY, allow_external=True,
                  generate=lambda q, e, o, **k: dict(answer="ok", model_calls=1))
    # 모델이 질문에 없는 이름을 대상으로 내면 쓰지 않는다.
    report = answer(QUESTION, tmp_path / "a", plan_transport=subject_plan("가람은행"),
                    directory=lambda: catalog(("run-1", "가람은행", "x")), mentions=lambda w, r=None: False, **common)
    assert report["documents"]["status"] == "none"
    # 문서 목록을 주지 않으면(사용자가 문서를 직접 고른 질문) 고르지 않는다.
    assert answer(QUESTION, tmp_path / "b", plan_transport=subject_plan("가람은행"), **common)["documents"] is None


def test_equal_scores_put_the_exactly_labelled_cell_first(monkeypatch):
    # '자본'은 '순자본비율' 안에 들어 있어 글자 일치로 같은 점수를 받는다. 점수가 같으면 라벨이 똑같은 칸이 앞선다.
    from types import SimpleNamespace
    from doc_understanding.rag.agentic import run as module

    def cell(table, number, row):
        return dict(table_id=table, cell_id=f"{table}/c{number}", value_raw=str(number), row_path=[row],
                    column_path=["2025년"], lookup_eligible=True, page=1)

    cells = dict(first=[cell("first", n, "자\n본") for n in range(1, 10)], second=[cell("second", 99, "순 자 본 비 율")])
    hits = [dict(run_id="r", payload=dict(entry_id=t, table_id=t, kind="table_row")) for t in ("first", "second")]
    monkeypatch.setattr(repository, "evidence_source", lambda rid: SimpleNamespace())
    monkeypatch.setattr(module, "table_outline", lambda doc, table_id: dict(table_id=table_id, rows=[], columns=[]))
    monkeypatch.setattr(module, "structure_matches", lambda doc, table_id, items, scope, similarity: [
        dict(c, matched_terms=["2025년", "순자본비율"], match_details=[], scope_matched=None, match_score=2.0)
        for c in cells[table_id]])
    monkeypatch.setattr(module, "resolve_evidence", lambda *a, **k: [])
    plan = QuestionPlan(requirements=[dict(question_quote="순자본비율", role="item", text="순자본비율"),
                                      dict(question_quote="2025년", role="condition", text="2025년")], queries=["q"])
    evidence, trace = module.gather("2025년 순자본비율", plan, "r", lambda q, rid, n: hits)
    assert evidence[0]["cell_id"] == "second/c99" and evidence[0]["evidence_id"] == "E1"
    assert [e["cell_id"] for e in evidence[1:3]] == ["first/c1", "first/c2"] and len(evidence) == 8


def test_a_failed_extra_answer_after_widening_keeps_the_abstention(tmp_path, api_key, kb_runtime):
    _, retriever, _ = kb_runtime
    calls = []

    def generate(question, evidence, output, **kwargs):
        calls.append(output.name)
        if len(calls) == 2:
            raise RuntimeError("ANSWER_CALL_FAILED_SEE_LOCAL_AUDIT")
        return dict(answer="보류", model_calls=1, selection=dict(evidence_ids=[], values_raw=[], abstained=True),
                    assessment_validation=dict(assessment=dict(requirements=[dict(question_quote=QUESTION,status='unsupported')])))

    report = answer(QUESTION, tmp_path / "run", run_id="run-1", retriever=retriever, plan_policy=POLICY,
                    allow_external=True, plan_transport=scripted(PLAN), generate=generate)
    # 표 전체로 넓혀 다시 답하는 호출이 실패해도 질문은 오류가 아니라 앞의 보류로 끝난다.
    assert calls == ["answer", "answer-whole"] and report["result"]["selection"]["abstained"]
    reading = report["attempts"][0]["trace"]["reading"]
    assert reading["used"] is False and reading["reason"] == "ANSWER_CALL_FAILED"
    # 남긴 결과는 넓히기 전 근거로 만든 것이므로, 돌려주는 근거와 기록의 번호도 넓히기 전 것이다.
    assert len(report["evidence"]) == reading["before"] < reading["after"]
    assert [i for i, _, _ in report["attempts"][0]["trace"]["evidence_ids"]] == [e["evidence_id"] for e in report["evidence"]]

    def failing_first(question, evidence, output, **kwargs):
        raise RuntimeError("ANSWER_CALL_FAILED_SEE_LOCAL_AUDIT")

    # 첫 답변 호출의 실패는 그대로 오류다.
    with pytest.raises(RuntimeError, match="ANSWER_CALL_FAILED"):
        answer(QUESTION, tmp_path / "first", run_id="run-1", retriever=retriever, plan_policy=POLICY,
               allow_external=True, plan_transport=scripted(PLAN), generate=failing_first)


# --- 원문 격자 읽기: 구조가 확정되지 않은 표 ---


def unreadable_runtime(monkeypatch):
    """검색에 걸린 표의 칸이 모두 조회 불가인 상황을 만든다(구조 해석이 표 전체를 보류한 표)."""
    from doc_understanding.rag.agentic import run as module
    real = module.table_outline
    monkeypatch.setattr(module, "table_outline", lambda doc, table_id: dict(real(doc, table_id), eligible=0))
    monkeypatch.setattr(module, "structure_matches", lambda *a, **k: [])
    monkeypatch.setattr(module, "resolve_evidence", lambda *a, **k: [])


def grid_reply(**fields):
    base = dict(kind="answered", answer_cells=[], row_label_cells=[], column_label_cells=[], answer_text="", explanation="")
    return {**base, **fields}


GRID = dict(table_id="t", page=6, cells=[dict(id="a", r=0, c=0, rs=1, cs=1, text="구분", bbox=None),
                                        dict(id="b", r=0, c=1, rs=1, cs=1, text="재예치기간", bbox=None),
                                        dict(id="c", r=1, c=0, rs=1, cs=1, text="일 단위 계좌", bbox=None),
                                        dict(id="d", r=1, c=1, rs=1, cs=1, text="12개월", bbox=None)])


def test_an_abstention_reads_the_raw_grid_of_a_table_without_usable_cells(tmp_path, api_key, kb_runtime, monkeypatch):
    _, retriever, _ = kb_runtime
    unreadable_runtime(monkeypatch)
    stages, loaded = [], []
    reply = grid_reply(answer_cells=["T0:r1c1"], row_label_cells=["T0:r1c0"], column_label_cells=["T0:r0c1"],
                       answer_text="재예치기간은 12개월입니다.")
    monkeypatch.setattr("doc_understanding.runtime.structured_call.http_transport", None)
    from doc_understanding.rag.agentic import grid as grid_module
    real_call = grid_module.structured_call

    def call(name, system, user, schema, policy, output, **kwargs):
        if name != "grid_answer":
            return real_call(name, system, user, schema, policy, output, **kwargs)
        output.mkdir(parents=True)
        return schema.model_validate(reply), dict(model="small-1", estimated_cost_usd=0.002, status="completed")

    monkeypatch.setattr(grid_module, "structured_call", call)
    report = answer(QUESTION, tmp_path / "run", run_id="run-1", retriever=retriever, plan_policy=POLICY, allow_external=True,
                    plan_transport=scripted(PLAN), progress=stages.append,
                    generate=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no evidence, so no answer call")),
                    grid_loader=lambda run_id, table_id: loaded.append(table_id) or dict(GRID, table_id=table_id),
                    grid_shapes=lambda run_id: [], grid_policy=POLICY)
    result = report["result"]
    assert result["generation_mode"] == "raw_grid_reading" and result["answer_text"] == "재예치기간은 12개월입니다."
    assert result["selection"] == dict(evidence_ids=["E1"], values_raw=["12개월"], abstained=False)
    assert report["evidence"][0]["lookup_eligible"] is False and report["evidence"][0]["row_path"] == ["일 단위 계좌"]
    assert stages[-1] == "grid" and len(loaded) == 2      # 검색 순위가 높은 표 두 개까지만 읽힌다
    trace = report["attempts"][0]["trace"]
    assert trace["grid"]["status"] == "answered" and report["grid_estimated_cost_usd"] == 0.002
    assert [i for i, _, _ in trace["evidence_ids"]] == ["E1"]


def test_a_rejected_or_failed_grid_reading_keeps_the_abstention(tmp_path, api_key, kb_runtime, monkeypatch):
    _, retriever, _ = kb_runtime
    unreadable_runtime(monkeypatch)
    from doc_understanding.rag.agentic import grid as grid_module
    real_call = grid_module.structured_call
    reply = dict(value=grid_reply(answer_cells=["T0:r1c1"], answer_text="12개월"))   # 라벨 칸을 대지 못한 답

    def call(name, system, user, schema, policy, output, **kwargs):
        if name != "grid_answer":
            return real_call(name, system, user, schema, policy, output, **kwargs)
        if reply["value"] is None:
            raise RuntimeError("MODEL_CALL_FAILED_SEE_LOCAL_AUDIT")
        return schema.model_validate(reply["value"]), dict(model="small-1", estimated_cost_usd=0.001)

    monkeypatch.setattr(grid_module, "structured_call", call)
    common = dict(run_id="run-1", retriever=retriever, plan_policy=POLICY, allow_external=True, generate=lambda *a, **k: None,
                  grid_loader=lambda run_id, table_id: dict(GRID, table_id=table_id), grid_shapes=None, grid_policy=POLICY)
    rejected = answer(QUESTION, tmp_path / "rejected", plan_transport=scripted(PLAN), **common)
    assert rejected["result"]["abstained"] and rejected["evidence"] == []
    assert rejected["attempts"][0]["trace"]["grid"]["reasons"] == ["NO_LABEL_CELL"]
    reply["value"] = None
    failed = answer(QUESTION, tmp_path / "failed", plan_transport=scripted(PLAN), **common)
    assert failed["result"]["abstained"] and failed["attempts"][0]["trace"]["grid"] == dict(status="failed", error="RuntimeError")
    # 읽기 설정이 없으면 시도하지 않는다.
    off = answer(QUESTION, tmp_path / "off", plan_transport=scripted(PLAN), run_id="run-1", retriever=retriever,
                 plan_policy=POLICY, allow_external=True, generate=lambda *a, **k: None)
    assert "grid" not in off["attempts"][0]["trace"]
