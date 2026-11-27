"""리랭커: 목록형 LLM 재정렬의 검증·대체·한도, 에이전트 흐름 연결. 외부 호출은 모의다."""
import json

import pytest

from doc_understanding.rag.agentic.rerank import LLMListwiseReranker, NoReranker, describe
from doc_understanding.runtime.structured_call import CallPolicy, Ledger
from tests.test_question_agent import PLAN, POLICY, QUESTION, kb_runtime, scripted  # noqa: F401

EVIDENCE = [dict(evidence_id=f"E{i}", cell_id=f"c{i}", table_id="t", page=1, row_path=[f"행 {i}"],
                 column_path=["이자율"], value_raw=f"{i}.0%\n둘째 줄", scope_context=[]) for i in range(1, 6)]


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-never-sent")


def reranker(*replies, **kwargs):
    return LLMListwiseReranker(POLICY, allow_external=True, transport=scripted(*replies), **kwargs)


def test_no_reranker_keeps_order_and_applies_limit():
    kept, record = NoReranker().rerank("q", None, EVIDENCE, 3)
    assert [e["cell_id"] for e in kept] == ["c1", "c2", "c3"] and record["dropped"] == 2


def test_describe_is_compact_and_single_line():
    d = describe(dict(EVIDENCE[0], value_raw="가" * 500 + "\n나"), {"r": "문서"})
    assert len(d["value"]) == 300 and "\n" not in d["value"] and d["kind"] == "table_cell"


def test_relevance_orders_evidence_and_ties_keep_original_order(tmp_path, api_key):
    reply = dict(ranking=[dict(id="C3", relevance=3), dict(id="C1", relevance=1), dict(id="C5", relevance=3),
                          dict(id="C2", relevance=0)])
    kept, record = reranker(reply).rerank("q", None, EVIDENCE, 4, tmp_path / "r")
    # C3·C5(3) → 점수 없는 C4(0.5)보다 C1(1)이 앞 → C4 → C2(0)는 한도 밖
    assert [e["cell_id"] for e in kept] == ["c3", "c5", "c1", "c4"]
    assert record["status"] == "completed" and record["unranked"] == ["C4"] and record["dropped"] == 1
    sent = json.loads((tmp_path / "r/request.json").read_text(encoding="utf8"))
    assert "C5" in sent["input"][1]["content"]


def test_unknown_and_duplicate_ids_are_ignored(tmp_path, api_key):
    reply = dict(ranking=[dict(id="C9", relevance=3), dict(id="C2", relevance=3), dict(id="C2", relevance=0)])
    kept, record = reranker(reply).rerank("q", None, EVIDENCE[:3], None, tmp_path / "r")
    assert [e["cell_id"] for e in kept] == ["c2", "c1", "c3"] and record["unranked"] == ["C1", "C3"]


def test_window_limits_what_the_model_sees_and_tail_follows(tmp_path, api_key):
    reply = dict(ranking=[dict(id="C2", relevance=3), dict(id="C1", relevance=0)])
    kept, record = reranker(reply, window=2).rerank("q", None, EVIDENCE, None, tmp_path / "r")
    assert [e["cell_id"] for e in kept] == ["c2", "c1", "c3", "c4", "c5"] and record["beyond_window"] == 3


def test_failure_or_disabled_call_keeps_original_order(tmp_path, api_key):
    kept, record = reranker("not a ranking").rerank("q", None, EVIDENCE, 2, tmp_path / "bad")
    assert [e["cell_id"] for e in kept] == ["c1", "c2"] and record["status"] == "failed"
    off = LLMListwiseReranker(POLICY, allow_external=False)
    kept, record = off.rerank("q", None, EVIDENCE, None, tmp_path / "off")
    assert len(kept) == 5 and record["reason"] == "EXTERNAL_CALL_NOT_ENABLED"


def test_ledger_budget_is_respected(tmp_path, api_key):
    tiny = Ledger(max_calls=0, max_total_usd=0.0)
    kept, record = reranker(dict(ranking=[]), ledger=tiny).rerank("q", None, EVIDENCE, 3, tmp_path / "r")
    assert record["status"] == "failed" and len(kept) == 3


def test_agent_reranks_renumbers_and_limits_evidence(tmp_path, api_key):
    from doc_understanding.rag.agentic.run import rerank_evidence
    trace = {}
    kept = rerank_evidence("q", None, EVIDENCE, trace,
                           reranker(dict(ranking=[dict(id="C4", relevance=3)])), 2, tmp_path / "r")
    assert [(e["evidence_id"], e["cell_id"]) for e in kept] == [("E1", "c4"), ("E2", "c1")]
    assert trace["rerank"]["kept"] == 2 and trace["evidence_ids"][0] == ("E1", "t", "c4")
    assert EVIDENCE[3]["evidence_id"] == "E4"  # 원본 근거는 바꾸지 않는다


def test_answer_flow_passes_reranked_evidence_to_generation(tmp_path, api_key, kb_runtime):  # noqa: F811
    from doc_understanding.rag.agentic.run import answer
    _, retriever, _ = kb_runtime
    seen = {}

    def generate(question, evidence, output, **kwargs):
        seen["evidence"] = evidence
        return dict(answer="ok", model_calls=1)

    report = answer(QUESTION, tmp_path / "run", run_id="run-1", retriever=retriever, plan_policy=POLICY,
                    allow_external=True, plan_transport=scripted(PLAN), generate=generate,
                    reranker=reranker(dict(ranking=[dict(id="C2", relevance=3)])), max_evidence=3)
    assert len(seen["evidence"]) == 3 and seen["evidence"][0]["evidence_id"] == "E1"
    assert report["attempts"][0]["trace"]["rerank"]["status"] == "completed"
    assert (tmp_path / "run/rerank/request.json").is_file() and report["rerank_estimated_cost_usd"] >= 0


def test_policy_is_explicit():
    with pytest.raises(Exception):
        CallPolicy(model="m")
