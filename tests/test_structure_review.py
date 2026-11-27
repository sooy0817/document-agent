"""구조 검토 에이전트: 근거 등급·판정, 적용 범위, 검토 후보 문서. 외부 모델 호출은 모의다."""
from pathlib import Path

import pytest

from doc_understanding.agents.structure_review.layout import decide_layout, grade
from tests.test_header_source_evidence import sample

ROOT = Path(__file__).resolve().parents[1]
KB_DOCUMENT = ROOT / "work/document-explorer/331a24bbcd404065b06605ee777fc3b2/document.json"


def kb_document():
    if not KB_DOCUMENT.is_file():
        pytest.skip("local KB baseline document is not available")
    from doc_understanding.domain.pipeline_contracts import CanonicalDocument
    return CanonicalDocument.model_validate_json(KB_DOCUMENT.read_bytes())


# --- 헤더 근거 등급과 판정 ---


def test_fill_band_with_digit_contrast_is_decided():
    t, p = sample()
    d = decide_layout(t, p)
    assert d.status == "decided" and d.choice == {"header_rows": 1, "stub_columns": 1}
    assert {"HEADER_FILL_BAND", "DIGIT_CONTRAST"} <= set(d.supports) and not d.counters
    assert d.source == "rule" and set(d.evidence_ids) >= {"r0c0", "r0c1", "r1c1"}


def test_fill_band_alone_is_decided_in_review_layer_only():
    # 규칙 분석기 기준선은 이 사례를 보류한다. 검토 계층은 강한 근거 하나로 결정하고 근거를 남긴다.
    t, p = sample("fill", ("A", "B"), ("C", "D"))
    assert decide_layout(t, p).status == "decided"


def test_bold_band_is_strong_evidence():
    t, p = sample("bold")
    assert "HEADER_BOLD_BAND" in decide_layout(t, p).supports


@pytest.mark.parametrize(
    "style,header,values,reason,counter",
    [
        ("all_fill", ("Category", "Observation"), ("13%", "27%"), "CONFLICT", "VALUE_CELLS_FILLED"),
        ("fill", ("1", "2"), ("13", "27"), "CONFLICT", "HEADER_LABEL_NOT_TEXTUAL"),
        ("fill", ("Same", "Same"), ("13", "27"), "CONFLICT", "DUPLICATE_HEADER_LABELS"),
    ],
)
def test_counter_evidence_blocks_decision(style, header, values, reason, counter):
    d = decide_layout(*sample(style, header, values))
    assert d.status == "ambiguous" and d.reason == reason and counter in d.counters


def test_single_medium_evidence_is_insufficient_and_no_style_is_no_evidence():
    d = decide_layout(*sample("none"))
    assert d.status == "ambiguous" and d.reason == "INSUFFICIENT_EVIDENCE"
    d = decide_layout(*sample("none", ("A", "B"), ("C", "D")))
    assert d.reason == "NO_EVIDENCE"


def test_unresolved_physical_cell_is_counter_evidence():
    t, p = sample()
    t = t.model_copy(update={"cells": tuple(c.model_copy(update={"status": "unresolved"})
                                            if c.cell_id == "r1c1" else c for c in t.cells)})
    assert "UNRESOLVED_PHYSICAL_CELL" in decide_layout(t, p).counters


def test_grading_never_mutates_source_table():
    t, p = sample()
    before = t.model_dump_json()
    grade(t, p)
    decide_layout(t, p)
    assert t.model_dump_json() == before


def test_kb_ambiguous_tables_split_into_rule_decided_unsupported_and_held():
    doc = kb_document()
    pages = {p.page_number: p for p in doc.document_ir.pages}
    structured = {t.table_id: t for t in doc.table_structure}
    results = {}
    for t in doc.physical_tables:
        if any(a.code == "HEADER_REGION_AMBIGUOUS" for a in structured[t.table_id].ambiguities):
            results[t.table_id.split("/", 1)[1]] = decide_layout(t, pages[t.page_number])
    decided = {k for k, d in results.items() if d.status == "decided"}
    assert decided == {"p0006/physical001", "p0008/physical000", "p0003/physical000", "p0007/physical000"}
    assert results["p0006/physical001"].supports == ("HEADER_FILL_BAND",)
    assert results["p0001/physical000"].reason == "NO_BODY_ROWS"
    # 첫 행까지 '짧은 라벨 | 긴 내용'인 2열 표는 머리글 없는 표로 결정한다(값은 행 경로만 가진다).
    assert results["p0007/physical000"].reason == "KEY_VALUE_ROWS"
    assert results["p0007/physical000"].choice == {"header_rows": 0, "stub_columns": 1}


# --- 적용 범위(소제목) 판정 ---

from doc_understanding.agents.structure_review.scope import decide_scopes, heading_like, scope_candidates  # noqa: E402


@pytest.mark.parametrize("text,following,expected", [
    ("[Section A] ", None, True),
    ("【 Type B 】", None, True),
    ("Overview", "- detail line", True),
    ("Overview", "plain continuation", False),
    ("- detail line", None, False),
    ("This sentence ends here.", "- detail", False),
    ("설명을 마칩니다", "- 항목", False),
    ("x" * 90, None, False),
    ("[Valid: 2025.01.01 ~ 2026.12.31]", None, False),
])
def test_heading_shape_uses_form_not_vocabulary(text, following, expected):
    assert heading_like(text, following) is expected


def test_wrapped_sentence_tail_is_not_a_heading():
    assert not heading_like("for these reasons", "- next item", previous="the rule applies")
    assert heading_like("Overview", "- next item", previous="Previous sentence ends.")


def test_only_headings_before_table_and_after_previous_table_are_candidates():
    texts = {"h1": "[First]", "d1": "- applies below", "h2": "[Second]", "d2": "- other", "h0": "[Old]"}
    items = [("content", "h0"), ("table", "t0"), ("content", "h1"), ("content", "d1"),
             ("table", "t1"), ("content", "h2"), ("content", "d2")]
    assert [c["content_id"] for c in scope_candidates(items, texts, "t1")] == ["h1"]
    assert [c["content_id"] for c in scope_candidates(items, texts, "t0")] == ["h0"]
    two = [("content", "h1"), ("content", "h2"), ("table", "t1")]
    assert len(scope_candidates(two, texts, "t1")) == 2


def test_kb_nested_rollover_table_scope_is_the_preceding_db_heading():
    doc = kb_document()
    decisions = {d.table_id.split("/", 1)[1]: d for d in decide_scopes(doc)}
    d = decisions["p0006/physical001"]
    assert d.status == "decided" and d.choice["text_raw"].strip() == "[확정급여형(DB)]"
    # 표 뒤에 있는 확정기여형 제목은 후보가 아니다.
    assert all("확정기여형" not in c["text_raw"] for c in d.candidates)
    # 줄바꿈된 문장 끝부분과 "항목: 값" 메타 표기는 제목 후보가 아니다.
    assert decisions["p0004/physical001"].choice["text_raw"].strip() == "[ 고정금리형 ]"
    assert decisions["p0001/physical000"].status == "not_applicable"


# --- 보류 판정의 모델 선택과 Judge (모의 전송) ---

import json  # noqa: E402

from doc_understanding.agents.structure_review.models import Decision  # noqa: E402
from doc_understanding.agents.structure_review.selector import resolve  # noqa: E402
from doc_understanding.runtime.structured_call import CallPolicy, Ledger  # noqa: E402

POLICY = CallPolicy(model="test-model", input_usd_per_million=1.0, output_usd_per_million=2.0)
SCOPE = Decision(table_id="doc/p0001/t1", aspect="scope", status="ambiguous", reason="MULTIPLE_HEADINGS",
                 evidence_ids=("h1", "h2"),
                 candidates=({"content_id": "h1", "text_raw": "[First]"}, {"content_id": "h2", "text_raw": "[Second]"}))


def scripted(*replies):
    queue = list(replies)

    def transport(payload, key, timeout):
        text = json.dumps(queue.pop(0))
        return dict(status="completed", usage={"input_tokens": 1, "output_tokens": 1},
                    output=[{"type": "message", "content": [{"type": "output_text", "text": text}]}])
    return transport


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-never-sent")


def test_model_choice_is_adopted_only_when_judge_agrees(tmp_path, api_key):
    t = scripted({"choice_id": "h2", "evidence_ids": ["h2"], "reason": "nearest"}, {"agree": True, "reason": "ok"})
    d = resolve(SCOPE, {}, POLICY, Ledger(), tmp_path, allow_external=True, transport=t)
    assert d.status == "decided" and d.source == "model" and d.choice == {"content_id": "h2", "text_raw": "[Second]"}
    t = scripted({"choice_id": "h2", "evidence_ids": ["h2"], "reason": "nearest"}, {"agree": False, "reason": "no"})
    d = resolve(SCOPE, {}, POLICY, Ledger(), tmp_path / "b", allow_external=True, transport=t)
    assert d.status == "ambiguous" and d.reason == "MODEL_JUDGE_DISAGREED"


@pytest.mark.parametrize("reply,reason", [
    ({"choice_id": "invented", "evidence_ids": [], "reason": "x"}, "MODEL_SELECTION_OUT_OF_OPTIONS"),
    ({"choice_id": "h1", "evidence_ids": ["not-in-input"], "reason": "x"}, "MODEL_SELECTION_OUT_OF_OPTIONS"),
    ({"choice_id": "none", "evidence_ids": [], "reason": "unclear"}, "MODEL_ABSTAINED"),
])
def test_invented_ids_and_abstention_keep_decision_held(tmp_path, api_key, reply, reason):
    d = resolve(SCOPE, {}, POLICY, Ledger(), tmp_path, allow_external=True, transport=scripted(reply))
    assert d.status == "ambiguous" and d.reason == reason and d.source == "rule"


def test_external_permission_and_budget_failures_are_recorded_not_raised(tmp_path, api_key):
    never = lambda *a: pytest.fail("no call")  # noqa: E731
    d = resolve(SCOPE, {}, POLICY, Ledger(), tmp_path, allow_external=False, transport=never)
    assert d.reason == "MODEL_SELECTION_FAILED:EXTERNAL_CALL_NOT_ENABLED"
    d = resolve(SCOPE, {}, POLICY, Ledger(max_calls=0), tmp_path / "b", allow_external=True, transport=never)
    assert d.reason == "MODEL_SELECTION_FAILED:MODEL_RUN_BUDGET_EXCEEDED"


def test_layout_selection_maps_to_explicit_analyzer_layout(tmp_path, api_key):
    t_, _ = sample("none", ("A", "B"), ("C", "D"))
    held = Decision(table_id=t_.table_id, aspect="layout", status="ambiguous", reason="NO_EVIDENCE",
                    evidence_ids=("r0c0",))
    from doc_understanding.agents.structure_review.selector import layout_context
    t = scripted({"choice_id": "header1_stub1", "evidence_ids": ["r0c0", "r0c1"], "reason": "labels"},
                 {"agree": True, "reason": "ok"})
    d = resolve(held, layout_context(t_), POLICY, Ledger(), tmp_path, allow_external=True, transport=t)
    assert d.choice == {"header_rows": 1, "stub_columns": 1} and d.source == "model"


# --- 검토 후보 문서 생성 ---

from doc_understanding.agents.structure_review import review as review_module  # noqa: E402
from doc_understanding.agents.structure_review.review import review_document  # noqa: E402


def test_kb_review_unlocks_values_without_touching_sources():
    doc = kb_document()
    before = doc.model_dump_json()
    reviewed, report = review_document(doc)
    assert doc.model_dump_json() == before
    assert reviewed.document_ir == doc.document_ir and reviewed.physical_tables == doc.physical_tables
    assert report["summary"]["usable_values_after"] > report["summary"]["usable_values_before"]
    assert not report["rejected"] and report["model_calls"] == []
    inner = next(t for t in reviewed.table_structure if t.table_id.endswith("p0006/physical001"))
    assert all(m.evidence_state == "inferred" for m in inner.cell_mappings)
    codes = [i.code for i in inner.issues]
    assert "STRUCTURE_REVIEW_LAYOUT" in codes and "STRUCTURE_REVIEW_SCOPE" in codes
    assert any(i.code == "STRUCTURE_REVIEW_CANDIDATE" for i in reviewed.issues)
    # 판정 대상이 아닌 표(걸친 칸도 머리글 모호도 없는 표)의 구조는 그대로다.
    untouched = {t.table_id: t for t in doc.table_structure}
    assert next(t for t in reviewed.table_structure if t.table_id.endswith("p0002/physical002")) == \
        untouched[next(k for k in untouched if k.endswith("p0002/physical002"))]


def test_reviewing_the_same_source_preserves_the_exact_snapshot_bytes():
    doc = kb_document()
    one, _ = review_document(doc)
    two, _ = review_document(doc)
    assert one is not None and two.model_dump_json(indent=2) == one.model_dump_json(indent=2)
    original = {t.table_id: t for t in doc.table_structure}
    changed = [t for t in one.table_structure if t != original[t.table_id]]
    assert any(t.analysis_run_id != original[t.table_id].analysis_run_id for t in changed)
    assert one.document_ir == doc.document_ir and one.physical_tables == doc.physical_tables


def test_rebuild_that_does_not_improve_is_rejected(monkeypatch):
    doc = kb_document()
    original = {t.table_id: t for t in doc.table_structure}
    monkeypatch.setattr(review_module.SemanticTableAnalyzer, "analyze",
                        lambda self, table, **kw: original[table.table_id])
    reviewed, report = review_document(doc)
    assert {r["reason"] for r in report["rejected"]} == {"REBUILD_NOT_IMPROVED"}
    # 적용 범위 판정은 레이아웃과 별개로 남는다.
    assert all(a["aspect"] == "scope" for a in report["applied"])


# --- 검토 후보가 색인·근거에 반영되는지 ---


def test_reviewed_scope_reaches_search_entries_evidence_and_catalog():
    from doc_understanding.rag.evidence.cells import cells_for_table
    from doc_understanding.rag.generation.answer import render_answer
    from doc_understanding.rag.generation.narrative import build_catalog
    from doc_understanding.rag.indexing.entries import make_entries

    doc = kb_document()
    reviewed, _ = review_document(doc)
    inner = next(t.table_id for t in reviewed.table_structure if t.table_id.endswith("p0006/physical001"))
    rows = [e for e in make_entries(reviewed) if e.get("table_id") == inner and e["kind"] == "table_row"]
    assert rows and all(e["primary_text"].startswith("적용 범위: [확정급여형(DB)]") for e in rows)
    # 원본 문서의 같은 표 항목에는 범위 줄이 없다(검토 전과 동일).
    assert not any("적용 범위:" in e["text"] for e in make_entries(doc))
    cells = cells_for_table(reviewed, inner)
    twelve = next(c for c in cells if c["value_raw"] == "12개월")
    assert twelve["lookup_eligible"] and twelve["structure_review"] == "layout_candidate"
    assert twelve["scope_context"][0]["text_raw"] == "[확정급여형(DB)]"
    twelve["evidence_id"] = "E1"
    catalog = build_catalog([twelve])
    assert any(s["kind"] == "scope_context" and s["text_raw"] == "[확정급여형(DB)]" for s in catalog)
    shown = render_answer(dict(evidence_ids=["E1"], values_raw=["12개월"], abstained=False), [twelve])
    assert "적용 범위(구조 검토 후보): [확정급여형(DB)]" in shown and "12개월" in shown


def test_text_header_over_numeric_body_is_strong_evidence_only_for_large_enough_tables():
    from doc_understanding.rag import repository
    from doc_understanding.services.corpus_rag import latest_runs
    try:
        run = next(r for r in latest_runs() if r["source_name"].startswith("강동농협"))
        doc = repository.fetch_document(run["run_id"])
    except Exception:
        pytest.skip("corpus database is not available")
    pages = {p.page_number: p for p in doc.document_ir.pages}
    table = next(t for t in doc.physical_tables if t.table_id.endswith("p0003/physical000"))
    d = decide_layout(table, pages[table.page_number])
    assert d.status == "decided" and "NUMERIC_BODY" in d.supports  # '구 분 | 2024년(A) | …' 아래 숫자 열
    # 작은 표(본문 2행)는 같은 모양이어도 강한 근거로 보지 않는다(기존 보수적 판정 유지).
    small = decide_layout(*sample("all_fill", ("Category", "Observation"), ("13%", "27%")))
    assert small.status == "ambiguous" and "NUMERIC_BODY" not in small.supports


def test_model_picks_only_among_rule_made_layout_candidates(tmp_path, api_key):
    from doc_understanding.agents.structure_review.selector import layout_context, options_for
    t_, _ = sample("none", ("A", "B"), ("C", "D"))
    candidates = ({"header_rows": 1, "stub_columns": 1, "row_stub_depths": [1, 1]},
                  {"header_rows": 0, "stub_columns": 1, "row_stub_depths": [1, 1]})
    held = Decision(table_id=t_.table_id, aspect="layout", status="ambiguous", reason="NO_EVIDENCE",
                    evidence_ids=("r0c0",), candidates=candidates)
    assert [o["id"] for o in options_for(held)] == ["layout_1", "layout_2", "keep_unresolved"]
    assert "no header row" in options_for(held)[1]["meaning"]
    t = scripted({"choice_id": "layout_2", "evidence_ids": ["r0c0"], "reason": "label then content in every row"},
                 {"agree": True, "reason": "ok"})
    d = resolve(held, layout_context(t_), POLICY, Ledger(), tmp_path, allow_external=True, transport=t)
    assert d.source == "model" and d.choice == candidates[1]
    # 후보에 없는 배치는 고를 수 없다.
    t = scripted({"choice_id": "layout_3", "evidence_ids": [], "reason": "x"})
    d = resolve(held, layout_context(t_), POLICY, Ledger(), tmp_path / "b", allow_external=True, transport=t)
    assert d.status == "ambiguous" and d.reason == "MODEL_SELECTION_OUT_OF_OPTIONS"


def test_review_calls_the_model_only_for_tables_the_rules_left_ambiguous(tmp_path, api_key):
    from doc_understanding.domain.pipeline_contracts import CanonicalDocument
    calls = []

    def transport(payload, key, timeout):
        calls.append(json.loads(payload["input"][1]["content"]))
        reply = ({"agree": True, "reason": "ok"} if "proposed" in calls[-1]
                 else {"choice_id": "layout_1", "evidence_ids": [], "reason": "short names over differing content"})
        return dict(status="completed", usage={"input_tokens": 1, "output_tokens": 1},
                    output=[{"type": "message", "content": [{"type": "output_text", "text": json.dumps(reply)}]}])
    # KB: 모든 표가 규칙으로 정해진다(머리글 근거·행별 깊이·라벨|내용·표를 담은 첫 행). 모델을 부르지 않는다.
    _, report = review_document(kb_document(), policy=POLICY, allow_external=True, output=tmp_path / "kb",
                                ledger=Ledger(max_calls=40), transport=transport)
    assert not [c for c in calls if c.get("task") == "layout"] and not report["rejected"]
    box = next(d for d in report["decisions"] if d["table_id"].endswith("p0005/physical000") and d["aspect"] == "layout")
    assert box["reason"] == "FIRST_ROW_HOLDS_TABLE" and box["choice"]["header_rows"] == 0 and box["source"] == "rule"
    # 수협: '구 분 | 내 용' 표만 규칙 근거가 없어 모델에 간다. 선택지는 규칙이 만든 두 배치와 보류뿐이다.
    source = ROOT / "work/document-explorer/ff772978ceb446498868945a347df6bd/document.json"
    if not source.is_file():
        pytest.skip("local Suhyup document is not available")
    doc = CanonicalDocument.model_validate_json(source.read_bytes())
    reviewed, report = review_document(doc, policy=POLICY, allow_external=True, output=tmp_path / "sh",
                                       ledger=Ledger(max_calls=40), transport=transport)
    asked = [c for c in calls if c.get("task") == "layout"]
    assert {c["context"]["table_id"].split("/", 1)[1] for c in asked} == {"p0001/physical000"}
    assert [o["id"] for o in asked[0]["options"]] == ["layout_1", "layout_2", "keep_unresolved"]
    table = next(t for t in reviewed.table_structure if t.table_id.endswith("p0001/physical000"))
    assert sum(m.evidence_state == "inferred" for m in table.cell_mappings) >= 10


def test_layout_that_makes_a_table_holding_cell_a_header_is_rejected(tmp_path, api_key):
    # 모델이 표를 담은 첫 행을 머리글로 고르고 Judge가 동의해도 받지 않는다.
    doc = kb_document()

    def transport(payload, key, timeout):
        user = json.loads(payload["input"][1]["content"])
        reply = ({"agree": True, "reason": "ok"} if "proposed" in user
                 else {"choice_id": "layout_1", "evidence_ids": [], "reason": "x"})
        return dict(status="completed", usage={"input_tokens": 1, "output_tokens": 1},
                    output=[{"type": "message", "content": [{"type": "output_text", "text": json.dumps(reply)}]}])
    table = next(t for t in doc.physical_tables if t.table_id.endswith("p0005/physical000"))
    physical = {t.table_id: t for t in doc.physical_tables}
    assert review_module.header_holds_table(table, 1, physical)
    assert not review_module.header_holds_table(
        next(t for t in doc.physical_tables if t.table_id.endswith("p0001/physical002")), 1, physical)


def test_layout_that_flattens_repeated_panels_is_rejected(monkeypatch):
    # 반복 머리글 패널의 라벨 열을 값으로 읽은 재분석 결과는 받지 않는다.
    from doc_understanding.domain.models import Issue
    doc = kb_document()
    real = review_module.SemanticTableAnalyzer.analyze

    def flattened(self, table, **kw):
        built = real(self, table, **kw)
        flag = Issue(code="REPEATED_STUB_AS_VALUE", severity="warning", message="test",
                     source_references=(table.source_references[0],))
        return built.model_copy(update={"issues": built.issues + (flag,)})

    monkeypatch.setattr(review_module.SemanticTableAnalyzer, "analyze", flattened)
    reviewed, report = review_document(doc)
    assert {r["reason"] for r in report["rejected"]} == {"REPEATED_PANELS_FLATTENED"}
    assert all(a["aspect"] == "scope" for a in report["applied"])
