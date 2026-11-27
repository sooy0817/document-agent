# 역할: 저장된 실행 결과(검색 후보·근거·답변)를 정답 ID와 비교해 항목별·전체 지표를 계산한다.
"""지표 정의

- hit@k: 검색 후보 상위 k개 중 하나라도 정답 셀·본문을 담고 있는가(후보의 cell_ids·content_id(s))
- evidence_any_hit: 정답 근거가 하나라도 있는가(evidence_recall은 이 값의 이전 이름).
- required_evidence_coverage / all_required_evidence: 필수 anchor별 충족률 / 모두 충족했는가.
- evidence@k, evidence_mrr: 근거 목록 안에서 정답의 순위(구조 일치·리랭커가 바꾸는 순서)
- answer_string_match: 보류하지 않은 답의 선택 값·답변 문장이 기대/금지 문자열 검사를 통과했는가.
- answer_correct: 위 검사를 통과하고, 여러 문서의 비교라면 실제 선택한 근거가 모든 필수 원본 위치를 포함하는가.
- abstain_correct: 답할 수 없는 항목을 보류했는가 / 답할 수 있는 항목을 보류하지 않았는가
- ambiguity_flagged: 필요한 조건을 되묻거나 가정과 원문 인용을 갖춘 답을 냈는가
- answer_totals(답변 모드만): 전체 대상 기준 집계. 실행 오류를 분모에서 빼지 않는다.
  answer_correct의 rate는 '정상 완료된 문항 중' 비율이므로 오류가 많을수록 부풀 수 있다. 전체 기준은 answer_totals를 본다.
수치는 사람 검토 전 개발용 평가셋 기준이며 일반화 성능 주장이 아니다.

평가 모드
- retrieval: 검색·근거까지만 실행한 설정(lexical, hybrid, agent, agent_rerank). 답변이 없는 것은 실패가 아니다.
- answer: 답변까지 실행한 설정(agent_answer). 답변이 없으면 실행 오류다.
"""
import re
from collections import Counter
from math import ceil

ANSWER_CONFIGS = {"agent_answer"}
METRIC_VERSION = "rag/5-explicit-equivalences"
ANSWER_SCORING = "versioned_expected_equivalents_and_same_document_anchor_groups"


def mode_of(config, runs=()):
    """설정 이름으로 평가 모드를 정한다. 모르는 이름이면 저장된 결과에 답변이 하나라도 있는지로 정한다."""
    if config in ANSWER_CONFIGS:
        return "answer"
    if config in ("lexical", "hybrid", "agent", "agent_rerank"):
        return "retrieval"
    return "answer" if any(r.get("result") is not None for r in runs) else "retrieval"


def norm(text):
    return re.sub(r"\s+", "", text or "")


def hit_ids(hit):
    payload = hit.get("payload", hit)
    ids = set(payload.get("cell_ids") or []) | set(payload.get("row_cell_ids") or [])
    ids |= {payload.get("content_id")} | set(payload.get("content_ids") or [])
    return {i for i in ids if i}


def evidence_ids(e):
    return {i for i in (e.get("cell_id"), e.get("content_id")) if i}


def selected_sources(evidence, selected):
    selected = set(selected) if isinstance(selected, list) else set()
    counts = Counter(e.get("evidence_id") for e in evidence)
    source_ids = set()
    for e in evidence:
        eid = e.get("evidence_id")
        if eid and eid in selected and counts[eid] == 1:
            source_ids.update(evidence_ids(e))
    return source_ids


def comparison_selection(anchors, groups, evidence, selected, *, min_documents=2):
    """선택된 E-ID를 그 실행의 원문 위치로 해석해 필수 위치별로 대조한다."""
    source_ids = selected_sources(evidence, selected)
    if groups is None or len(groups) != len(anchors):
        missing = list(anchors)
    else:
        missing = [anchor for anchor, ids in zip(anchors, groups) if source_ids.isdisjoint(ids)]
    return dict(selected_gold_count=len(anchors) - len(missing), required_gold_count=len(anchors),
                missing_selected_gold_docs=sorted({anchor.doc for anchor in missing}),
                selected_all_required_evidence=not missing and len({a.doc for a in anchors}) >= min_documents)


def has_terms(text, groups):
    folded = norm(text).casefold()
    return all(any(norm(term).casefold() in folded for term in group) for group in groups)


def forbidden_value(text, value):
    term = norm(value)
    if not term:
        return False
    # A prohibited 0% must not reject a quoted factor of 30%.
    left = r"(?<![\d.])" if term[0].isdigit() else ""
    right = r"(?![\d.])" if term[-1].isdigit() else ""
    return re.search(left + re.escape(term) + right, norm(text), re.IGNORECASE) is not None


def expected_match(text, term, equivalents):
    if not equivalents:
        return norm(term) in norm(text)  # Preserve v1 behavior without an explicit revision.
    for candidate in [term,*equivalents]:
        token=norm(candidate)
        if not token:
            continue
        # Do not credit 12.5 for 2.5, negative for positive, or a different unit.
        left=r'(?<![\w.,+\-−])'
        right=r'(?![\d.])' if token[-1].isdigit() else r'(?![A-Za-z])' if token[-1].isascii() and token[-1].isalpha() else ''
        pattern=r'\s*'.join(re.escape(c) for c in token)
        if re.search(left+pattern+right,text,re.I):
            return True
    return False


def ambiguity_outcome(item, resolved, result, evidence, abstained):
    ask = result.get("clarification") or {}
    text = result.get("answer_text") or result.get("answer") or ""
    if abstained:
        choices = {doc.get("run_id") for doc in ask.get("documents", [])} - {None, ""}
        quote = ask.get("question_quote") or ""
        context = (ask.get("kind") == "needs_context" and quote and quote in item.question
                   and len({s.strip() for s in ask.get("readings", []) if s.strip()}) >= 2)
        is_request = re.search(r"[?？]|(?:알려|골라|말씀해|확인해)\s*주(?:세요|십시오)", text)
        specific = ((ask.get("kind") == "needs_choice" and len(choices) >= 2)
                    or (is_request and (context or bool(item.clarification_terms))))
        if specific and has_terms(text, item.clarification_terms):
            return "clarification"
        return "insufficient_clarification"

    def supported(reading, ids, anchors, groups):
        sources = selected_sources(evidence, ids)
        answer = norm(reading).casefold().replace("×", "x")
        return any(sources.intersection(group) and has_terms(reading, anchor.conditional_terms)
                   and any(norm(v).casefold().replace("×", "x") in answer for v in (anchor.text, *anchor.value_aliases))
                   for anchor, group in zip(anchors, groups))

    selected = (result.get("selection") or {}).get("evidence_ids")
    primary = resolved.get("gold_groups") or ([resolved["gold"]] if len(item.gold) == 1 else [])
    secondary = resolved.get("alternative_groups") or ([resolved["alternative"]] if len(item.alternative_gold) == 1 else [])
    alternatives = result.get("alternatives") or []
    branches = [(text, selected), *((a.get("interpretation", ""), a.get("evidence_ids")) for a in alternatives)]
    if any(forbidden_value(reading, word) for reading, _ in branches for word in item.forbidden):
        return "unsupported_interpretation"
    anchored = [(a, g) for anchors, groups in ((item.gold, primary), (item.alternative_gold, secondary))
                for a, g in zip(anchors, groups) if a.conditional_terms]
    if anchored:
        anchors, groups = zip(*anchored)
        if all(supported(s, ids, anchors, groups) for s, ids in branches):
            return "grounded_condition"
    elif (alternatives and supported(text, selected, item.gold, primary)
          and all(supported(s, ids, item.alternative_gold, secondary) for s, ids in branches[1:])):
        return "grounded_condition"
    return "unsupported_interpretation"


def score_item(item, resolved, run, ks=(1, 3, 5, 10), mode=None):
    """mode: 'answer' | 'retrieval' | None(저장 결과에 답변이 있으면 answer로 본다 — 예전 호출과 같은 동작)."""
    gold = set(resolved["gold"])
    groups = resolved.get("gold_groups")
    # 옛 단일 anchor 호출은 호환한다. 여러 anchor의 합집합만 있으면 충족률은 알 수 없다.
    if groups is None and len(item.gold) == 1:
        groups = [gold]
    unresolved_gold = resolved.get("unresolved_gold", resolved["unresolved"])
    hits = run.get("hits") or []
    first = next((i for i, h in enumerate(hits, 1) if hit_ids(h) & gold), None)
    out = dict(id=item.id, type=item.type, first_gold_rank=first,
               gold_resolved=not item.gold or (bool(gold) and not unresolved_gold
                                               and groups is not None and len(groups) == len(item.gold)
                                               and all(groups)), unresolved=resolved["unresolved"])
    if mode is None:
        mode = "answer" if run.get("result") is not None else "retrieval"
    out["mode"] = mode
    # 실행 오류: 실행기가 남긴 오류, 또는 답변 모드인데 답변 결과가 없는 것.
    out["run_error"] = bool(run.get("error")) or (mode == "answer" and run.get("result") is None)
    out["expects"] = "ambiguous" if item.ambiguous else "answer" if item.answerable else "abstain"
    if item.gold:
        for k in ks:
            out[f"hit@{k}"] = first is not None and first <= k
        out["mrr"] = 1 / first if first else 0.0
        evidence = run.get("evidence") or []
        rank = next((i for i, e in enumerate(evidence, 1) if evidence_ids(e) & gold), None)
        out["evidence_recall"] = rank is not None
        out["evidence_any_hit"] = rank is not None
        evidence_found = set().union(*(evidence_ids(e) for e in evidence))
        if groups is not None and len(groups) == len(item.gold):
            covered = sum(bool(set(group) & evidence_found) for group in groups)
            out["required_evidence_coverage"] = covered / len(item.gold)
            out["all_required_evidence"] = covered == len(item.gold)
        out["evidence_first_rank"] = rank
        for k in (1, 3):
            out[f"evidence@{k}"] = rank is not None and rank <= k
        out["evidence_mrr"] = 1 / rank if rank else 0.0
    result = run.get("result")
    if result is not None:
        selection = result.get("selection") or {}
        abstained = bool(selection.get("abstained", result.get("abstained", False)))
        out["abstained"] = abstained
        if item.answerable and not item.ambiguous:
            out["abstain_correct"] = not abstained
            text = " ".join(selection.get("values_raw") or []) + " " + (result.get("answer_text") or "")
            matched = (not abstained and bool(item.expected)
                       and all(expected_match(text,x,item.expected_equivalents.get(x,[])) for x in item.expected)
                       and not any(forbidden_value(text, x) for x in item.forbidden))
            out["answer_string_match"] = matched
            out["answer_correct"] = matched
            if item.require_selected_evidence or item.type == "multi_document" or len({a.doc for a in item.gold}) > 1:
                out.update(comparison_selection(item.gold, groups, run.get("evidence") or [], selection.get("evidence_ids"),
                                                min_documents=2 if item.type == "multi_document" else 1))
                out["answer_correct"] = matched and out["gold_resolved"] and out["selected_all_required_evidence"]
        elif not item.answerable:
            out["abstain_correct"] = abstained
        if item.ambiguous:
            out["ambiguity_outcome"] = ambiguity_outcome(item, resolved, result, run.get("evidence") or [], abstained)
            out["ambiguity_flagged"] = out["ambiguity_outcome"] in ("clarification", "grounded_condition")
    out["cost_usd"] = run.get("cost_usd")
    out["seconds"] = run.get("seconds")
    return out


def success(row):
    """그 문항에 기대한 결과를 냈는가: 답할 문항은 정답, 답이 없는 문항은 보류, 모호한 문항은 모호 표시."""
    key = dict(answer="answer_correct", abstain="abstain_correct", ambiguous="ambiguity_flagged")[row["expects"]]
    return bool(row.get(key)) and not row.get("run_error")


def answer_totals(rows):
    """답변 모드의 전체 대상 기준 집계. 실행 오류도 분모에 넣는다(오류는 정답도 보류도 아닌 실패).

    - total: 전체 대상 문항 / completed: 답변 결과가 있는 문항 / run_errors: 실행 오류
    - abstained: 정상 완료 중 보류한 문항(답이 없는 문항을 맞게 보류한 것 포함)
    - answerable: 답할 수 있는 문항만 따로 — correct, abstained(불필요한 보류), wrong, run_errors,
      rate_of_total(정답 ÷ 전체 대상), rate_of_completed(정답 ÷ 정상 완료)
    - expected_outcome: 문항마다 기대한 결과(정답·보류·모호 표시)를 낸 수와 전체 대상 기준 비율
    """
    def ratio(a, b):
        return round(a / b, 3) if b else None
    errors = [r for r in rows if r.get("run_error")]
    done = [r for r in rows if not r.get("run_error")]
    able = [r for r in rows if r["expects"] == "answer"]
    able_done = [r for r in able if not r.get("run_error")]
    correct = sum(bool(r.get("answer_correct")) for r in able_done)
    held = sum(bool(r.get("abstained")) for r in able_done)
    met = sum(success(r) for r in rows)
    return dict(
        total=len(rows), completed=len(done), run_errors=len(errors), run_error_ids=[r["id"] for r in errors],
        abstained=sum(bool(r.get("abstained")) for r in done),
        answerable=dict(total=len(able), completed=len(able_done), correct=correct, abstained=held,
                        wrong=len(able_done) - correct - held, run_errors=len(able) - len(able_done),
                        rate_of_total=ratio(correct, len(able)), rate_of_completed=ratio(correct, len(able_done))),
        expected_outcome=dict(met=met, total=len(rows), rate_of_total=ratio(met, len(rows))))


def summarize(rows, mode=None):
    """mode를 주지 않으면 채점된 문항의 모드를 따른다(섞여 있으면 answer)."""
    if mode is None:
        mode = "answer" if any(r.get("mode") == "answer" for r in rows) else "retrieval"
    def rate(key):
        values = [r[key] for r in rows if key in r and r[key] is not None]
        return dict(n=len(values), rate=round(sum(map(float, values)) / len(values), 3) if values else None)
    keys = ["hit@1", "hit@3", "hit@5", "hit@10", "mrr", "evidence_recall", "evidence_any_hit",
            "required_evidence_coverage", "all_required_evidence", "selected_all_required_evidence",
            "answer_string_match", "evidence@1", "evidence@3",
            "evidence_mrr", "answer_correct",
            "abstain_correct", "ambiguity_flagged"]
    summary = {k: rate(k) for k in keys}
    summary["metric_version"] = METRIC_VERSION
    summary["answer_scoring"] = ANSWER_SCORING
    summary["mode"] = mode
    summary["items"] = len(rows)
    summary["run_errors"] = sum(bool(r.get("run_error")) for r in rows)
    # 검색만 실행한 평가에는 답변 집계를 만들지 않는다(답변이 없는 것을 실패로 세지 않는다).
    summary["answer_totals"] = answer_totals(rows) if mode == "answer" else None
    seconds = [r["seconds"] for r in rows if r.get("seconds") is not None]
    costs = [r["cost_usd"] for r in rows if r.get("cost_usd") is not None]
    summary["mean_seconds"] = round(sum(seconds) / len(seconds), 2) if seconds else None
    summary["latency_seconds"] = latency(seconds)
    summary["timing_missing_items"] = [r["id"] for r in rows if r.get("seconds") is None]
    summary["cost_missing_items"] = [r["id"] for r in rows if r.get("cost_usd") is None]
    summary["observed_cost_usd"] = round(sum(costs), 5) if costs else None
    summary["total_cost_usd"] = summary["observed_cost_usd"] if len(costs) == len(rows) else None
    summary["unresolved_gold_items"] = [r["id"] for r in rows if not r["gold_resolved"]]
    by_type = {}
    for r in rows:
        by_type.setdefault(r["type"], []).append(r)
    summary["by_type"] = {t: {k: rate_of(v, k) for k in ("hit@5", "evidence@1", "evidence_recall", "answer_correct")}
                          for t, v in by_type.items()}
    return summary


def latency(values):
    """기록된 실행(실패 포함)의 nearest-rank 분위수. 누락 항목은 별도로 보고한다."""
    ordered = sorted(values)
    return dict(n=len(ordered), **{name: ordered[ceil(q * len(ordered)) - 1] if ordered else None
                                  for name, q in (("p50", .5), ("p90", .9), ("p95", .95), ("p99", .99), ("max", 1))})


def rate_of(rows, key):
    values = [r[key] for r in rows if key in r and r[key] is not None]
    return round(sum(map(float, values)) / len(values), 3) if values else None
