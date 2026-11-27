"""누락·부분 근거·실패 비용을 숨기지 않는 평가 회귀 검사. DB와 모델을 사용하지 않는다."""
from types import SimpleNamespace
import json

import pytest

from doc_understanding.evaluation.mapping.dataset import ExpectedCell, ExpectedTable, MappingSet
from doc_understanding.evaluation.mapping.score import score
from doc_understanding.evaluation.rag.anchors import resolve_item
from doc_understanding.evaluation.rag.dataset import EvalItem
from doc_understanding.evaluation.rag.metrics import score_item, summarize
from doc_understanding.evaluation.rag.dataset import EvalSet
from doc_understanding.evaluation.rag.snapshot import read_snapshot, write_snapshot


def mapping():
    return MappingSet(name="test", status="draft", reviewed_by_human=False, documents={"d": "doc"}, tables=[
        ExpectedTable(doc="d", table="p0001/physical001", cells=[
            ExpectedCell(r=0, c=0, text="1", role="value", row_path=["r"]),
            ExpectedCell(r=0, c=1, text="2", role="value", row_path=["r"]),
            ExpectedCell(r=0, c=2, text="box", role="container"),
        ])])


def test_missing_table_keeps_every_expected_cell_and_container_in_denominators():
    s = score(mapping(), {})["summary"]
    assert s["missing_tables"] == 1 and s["missing_table_cells"] == 3
    assert s["role_accuracy"] == dict(n=3, rate=0.0)
    assert s["exact"] == dict(n=2, rate=0.0)
    assert s["container_found"] == dict(n=1, rate=0.0)


@pytest.mark.parametrize("second", [[], [dict(r=0, c=1, text="wrong")]])
def test_missing_or_changed_cell_cannot_inflate_exact_score(second):
    view = dict(cells=[dict(r=0, c=0, text="1", role="value", eligible=True, blockers=[],
                           row_path=["r"], column_path=[])] + second)
    s = score(mapping(), {"doc/p0001/physical001": view})["summary"]
    assert s["exact"] == dict(n=2, rate=0.5)
    assert s["exact_matched"] == dict(n=1, rate=1.0)
    assert s["cell_mismatch"] == 2


def item():
    return EvalItem(id="q", type="synthesis", question="both?", expected=["a", "b"],
                    gold=[dict(doc="d", page=1, text=t) for t in ("a", "b")])


def test_required_anchors_stay_separate_and_partial_resolution_is_reported():
    doc = SimpleNamespace(physical_tables=[SimpleNamespace(page_number=1, cells=[
        SimpleNamespace(cell_id="a", original_text="a")])], contents=[])
    resolved = resolve_item(item(), {"d": "doc"}, {"doc": doc})
    assert resolved["gold_groups"] == [{"a"}, set()]
    row = score_item(item(), resolved, dict(evidence=[dict(cell_id="a")], hits=[]), mode="retrieval")
    assert not row["gold_resolved"] and row["evidence_any_hit"]
    assert row["required_evidence_coverage"] == 0.5 and not row["all_required_evidence"]
    assert summarize([row])["unresolved_gold_items"] == ["q"]


def test_any_hit_and_all_required_evidence_are_distinct():
    resolved = dict(gold={"a", "b", "a-alternative"}, gold_groups=[{"a", "a-alternative"}, {"b"}], unresolved=[])
    partial = score_item(item(), resolved, dict(evidence=[dict(cell_id="a-alternative")]), mode="retrieval")
    full = score_item(item(), resolved, dict(evidence=[dict(cell_id="a"), dict(content_id="b")]), mode="retrieval")
    assert partial["evidence_any_hit"] and partial["required_evidence_coverage"] == 0.5
    assert full["required_evidence_coverage"] == 1 and full["all_required_evidence"]


def test_unresolved_gold_is_still_a_retrieval_target():
    row = score_item(item(), dict(gold=set(), gold_groups=[set(), set()], unresolved=[{}]), {}, mode="retrieval")
    assert summarize([row])["hit@5"] == dict(n=1, rate=0.0)
    assert summarize([row])["all_required_evidence"] == dict(n=1, rate=0.0)


def test_legacy_union_does_not_pretend_to_measure_required_anchor_coverage():
    row = score_item(item(), dict(gold={"a", "b"}, unresolved=[]), dict(evidence=[dict(cell_id="a")]))
    assert "required_evidence_coverage" not in row and not row["gold_resolved"]


def test_failure_latency_and_cost_are_counted_and_missing_cost_is_not_zero():
    resolved = dict(gold=set(), gold_groups=[set(), set()], unresolved=[{}])
    runs = [dict(error="timeout", seconds=30, cost_usd=.2), dict(seconds=1, cost_usd=0), dict(error="unknown")]
    rows = [score_item(item(), resolved, r, mode="retrieval") for r in runs]
    s = summarize(rows)
    assert s["mean_seconds"] == 15.5 and s["latency_seconds"]["p95"] == 30
    assert s["observed_cost_usd"] == .2 and s["total_cost_usd"] is None
    assert s["cost_missing_items"] == ["q"] and s["timing_missing_items"] == ["q"]


def test_string_matching_is_explicitly_not_semantic_accuracy():
    row = score_item(item(), dict(gold={"a", "b"}, gold_groups=[{"a"}, {"b"}], unresolved=[]),
                     dict(result=dict(answer_text="a is not b", selection=dict(values_raw=[]))))
    assert row["answer_string_match"]  # 의미가 틀려도 문자열은 들어갈 수 있음을 고정한다.
    assert row["answer_correct"]  # 단일 문서 채점은 종전 문자열 검사를 유지한다.


def snapshot(folder):
    data = EvalSet(name="test", status="draft", reviewed_by_human=False, documents={"d": "doc"}, items=[item()])
    resolved = dict(gold={"a", "b"}, alternative=set(), gold_groups=[{"a"}, {"b"}], unresolved=[])
    write_snapshot(folder, data, data.items, {"q": resolved},
                   provenance=dict(configs=["agent_answer"], index_version="test-v1"))
    return data


def test_snapshot_rejects_changed_dataset_and_tampered_references(tmp_path):
    data = snapshot(tmp_path)
    assert read_snapshot(tmp_path, data)["resolved"]["q"]["gold_groups"] == [["a"], ["b"]]
    changed = data.model_copy(deep=True)
    changed.items[0].expected.append("different")
    with pytest.raises(ValueError, match="DATASET_CHANGED"):
        read_snapshot(tmp_path, changed)
    path = tmp_path / "evaluation-snapshot.json"
    text = json.loads(path.read_text(encoding="utf-8"))
    text["payload"]["resolved"]["q"]["gold"] = ["other"]
    path.write_text(json.dumps(text), encoding="utf-8")
    with pytest.raises(ValueError, match="SNAPSHOT_CHANGED"):
        read_snapshot(tmp_path, data)


def test_rescore_uses_saved_references_and_counts_missing_result_without_db(tmp_path, monkeypatch):
    from scripts import evaluate_rag
    folder = tmp_path / "run"
    folder.mkdir()
    data = snapshot(folder)
    monkeypatch.chdir(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("rescore must not query the current corpus or run a model")
    monkeypatch.setattr(evaluate_rag, "latest_runs", forbidden)
    monkeypatch.setattr(evaluate_rag, "run_item", forbidden)
    monkeypatch.setattr(evaluate_rag, "commit", lambda: "test")
    out = evaluate_rag.rescore(data, folder)
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    s = report["configs"]["agent_answer"]["summary"]
    assert s["answer_totals"]["answerable"]["total"] == 1 and s["run_errors"] == 1
    assert report["index_version"] == "test-v1"
    with pytest.raises(FileExistsError):
        snapshot(folder)


def test_legacy_rescore_refuses_to_silently_use_current_corpus(tmp_path):
    from scripts import evaluate_rag
    data = EvalSet(name="test", status="draft", reviewed_by_human=False, documents={"d": "doc"}, items=[item()])
    with pytest.raises(ValueError, match="SNAPSHOT_REQUIRED"):
        evaluate_rag.rescore(data, tmp_path)


def test_runner_saves_snapshot_and_elapsed_time_even_when_a_query_fails(tmp_path, monkeypatch):
    from scripts import evaluate_rag
    data = EvalSet(name="test", status="draft", reviewed_by_human=False, documents={"d": "doc"}, items=[item()])
    path = tmp_path / "set.json"
    path.write_text(data.model_dump_json(), encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("sys.argv", ["evaluate_rag", "--set", str(path), "--configs", "lexical"])
    monkeypatch.setattr(evaluate_rag, "latest_runs", lambda: [])
    monkeypatch.setattr(evaluate_rag, "commit", lambda: "test")
    def failed(*args):
        raise TimeoutError("test")
    monkeypatch.setattr(evaluate_rag, "run_item", failed)
    evaluate_rag.main()
    folder = next((tmp_path / "work/rag-eval").iterdir())
    recorded = json.loads((folder / "lexical/q.json").read_text(encoding="utf-8"))
    assert recorded["error"] == "TimeoutError" and recorded["seconds"] >= 0 and recorded["cost_usd"] is None
    assert read_snapshot(folder, data)["provenance"]["configs"] == ["lexical"]


def test_batch_audit_detects_content_change_and_missing_output_not_just_counts():
    from scripts.check_evaluation_readiness import compare
    a = dict(name="a", outcomes={"doc": "done"}, result_hashes={"doc": "original"},
             missing_result_files=[], duplicate_inputs=0)
    b = dict(a, name="b", result_hashes={"doc": "changed"})
    assert compare(a, b)["same_outcomes"] and compare(a, b)["changed_results"] == ["doc"]
    missing = dict(b, result_hashes={}, missing_result_files=["doc"])
    assert not compare(a, missing)["complete_result_coverage"]


def test_evaluation_directory_stays_within_selected_current_runs(monkeypatch):
    from doc_understanding.rag.agentic import directory

    def no_global_corpus():
        pytest.fail("a scoped evaluation must not load the global document directory")

    monkeypatch.setattr(directory.catalog, "current_runs", no_global_corpus)
    def lookup(ids):
        assert ids == ["selected", "old", "other-index"]
        return {rid: dict(run_id=rid, document_id=rid, name=rid, source_name=rid, pages=1,
                          is_current=current, index_version=version)
                for rid, current, version in [("selected", True, "v1"), ("old", False, "v1"),
                                              ("other-index", True, "v0")]}
    monkeypatch.setattr(directory.catalog, "lookup", lookup)
    def views(ids):
        assert ids == ["selected"]
        return {rid: dict(organization="", title="", document_type="", as_of="", aliases=[]) for rid in ids}
    monkeypatch.setattr(directory.store, "view", views)
    assert [d["run_id"] for d in directory.documents("v1", ["selected", "old", "other-index"])] == ["selected"]


@pytest.mark.parametrize("run_ids", [["selected"], []])
def test_evaluation_mentions_keeps_explicit_scope_even_when_empty(monkeypatch, run_ids):
    from doc_understanding.rag.agentic import directory

    calls = []
    def query(sql, params):
        calls.append((sql, params))
        return []
    monkeypatch.setattr(directory, "query_params", query)
    assert not directory.mentions("v1", run_ids)("needle")
    assert "e.run_id = ANY(%s)" in calls[0][0]
    assert calls[0][1] == [run_ids, "needle"]
