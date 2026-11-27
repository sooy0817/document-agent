# 역할: 질문 에이전트의 각 단계(계획, 검색·구조 일치·문맥 보충, 근거 선별, 답변)를 구현하고 그래프(graph.py)로 실행한다.
"""Plan-then-execute question agent (ADR-079).

LLM은 계획(make_plan)과 기존 답변 생성에서만 호출한다. 도구 순서·호출 수는 코드가 정한다.
실행 순서와 분기는 agentic/graph.py(LangGraph)에 있고, 이 파일은 각 노드가 하는 일을 정한다.
답변 검사에서 근거 부족으로 판정된 질문 구간이 있으면 그 구간으로 한 번만 다시 검색한다.
"""
import json
from itertools import zip_longest
from pathlib import Path

from doc_understanding.rag import repository
from doc_understanding.rag.agentic import documents as document_selection
from doc_understanding.rag.agentic import grid as grid_reading
from doc_understanding.rag.agentic import recovery
from doc_understanding.rag.agentic.plan import fallback_plan, make_plan
from doc_understanding.rag.agentic.reading import expand_units
from doc_understanding.rag.agentic.matching import compact
from doc_understanding.rag.agentic.tools import structure_matches, table_outline
from doc_understanding.rag.service import generate_answer, resolve_evidence
from doc_understanding.runtime.structured_call import Ledger
from doc_understanding.runtime.telemetry import span


def _write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf8")


def merge_hits(batches, limit=12):
    """여러 검색 결과를 첫 등장 순위로 합친다. 같은 실행·항목은 한 번만 남긴다."""
    seen, out = set(), []
    for rank in range(max((len(b) for b in batches), default=0)):
        for batch in batches:
            if rank < len(batch):
                hit = batch[rank]
                key = (hit["run_id"], hit["payload"]["entry_id"])
                if key not in seen:
                    seen.add(key)
                    out.append(hit)
    return out[:limit]


def terms_of(plan):
    # 계획의 역할 분류는 틀릴 수 있다(예: 열 이름을 적용 범위로 분류). 구조 일치에는 모든 요구 사항
    # 문구를 쓰고, 적용 범위 역할은 범위 가감점에만 추가로 쓴다.
    items = [t for r in plan.requirements for t in (r.text, r.question_quote)]
    scope = [t for r in plan.requirements if r.role == "scope" for t in (r.text, r.question_quote)]
    return list(dict.fromkeys(items)), list(dict.fromkeys(scope))


SMALL_DOCUMENT_TABLES = 15


def gather(question, plan, run_id, retriever, *, max_tables=5, max_structured=8, max_documents=1,
           max_cells_per_document=8, extra_queries=(), progress=None, whole_document=None, retry_only=False):
    """검색·구조 일치·문맥 보충을 실행하고 근거 목록과 추적 정보를 반환한다.
    progress(단계 이름)를 주면 지금 실행하는 단계를 알린다(searching → evidence). 결과에는 영향이 없다.
    whole_document(실행 ID): 검색 범위가 이 문서 하나로 정해졌을 때 준다. 표가 SMALL_DOCUMENT_TABLES개 이하인 문서면
    검색에 걸리지 않은 표도 구조 일치 후보에 넣는다(범위가 문서 하나면 그 문서의 표 라벨을 모두 볼 수 있다. 검색은
    라벨의 띄어쓰기·표기 차이로 맞는 표를 놓칠 수 있다)."""
    report = progress or (lambda stage: None)
    report("searching")
    queries = list(dict.fromkeys(extra_queries if retry_only else [*plan.queries, question, *extra_queries]))
    many = getattr(retriever, "many", None)
    if many is not None:
        # 검색어 전체를 한 번에 검색한다(임베딩 1회, SQL 몇 번). 결과는 하나씩 검색한 것과 같다.
        with span("agent.search", dict(queries=queries, run_id=run_id)) as box:
            batches = many(queries, 8)
            box["output"] = dict(candidates=[h for batch in batches for h in batch[:3]])
    else:
        batches = []
        for q in queries:
            with span("agent.search", dict(query=q, run_id=run_id)) as box:
                batches.append(retriever(q, run_id, 8))
                box["output"] = dict(candidates=batches[-1])
    hits = merge_hits(batches)
    report("evidence")
    items, scope = terms_of(plan)
    docs, structured, outlines = {}, [], []
    # 내용을 담는 칸이 있는 행이 검색되면 그 칸 안에 담긴 표도 바로 뒤에 후보로 둔다.
    tables = list(dict.fromkeys((h["run_id"], t) for h in hits if h["payload"].get("table_id")
                                for t in [h["payload"]["table_id"], *(h["payload"].get("child_table_ids") or [])]))
    tables = tables[:max_tables]
    if whole_document:
        rest = [(whole_document, t) for t in repository.table_ids(whole_document)]
        if len(rest) <= SMALL_DOCUMENT_TABLES:
            tables = list(dict.fromkeys(tables + rest))
    for rid, table_id in tables:
        # 문서 전체가 아니라 적재 때 저장한 근거 출처(표·본문 단위)를 읽는다. 질문 한 건 안에서는 실행마다 하나를 함께 쓴다.
        if rid not in docs:
            docs[rid] = repository.evidence_source(rid)
        outlines.append(dict(table_outline(docs[rid], table_id), run_id=rid))
    named = (grid_reading.withheld_row(outlines, question, plan.queries)
             if whole_document and getattr(plan, "reading", "focused") == "focused" else None)
    if named:
        outline, label = named
        table_id = outline["table_id"]
        row_ids = {c["cell_id"] for c in docs[whole_document].cells(table_id)
                   if c["row_path"] and compact(c["row_path"][-1]) == compact(label)}
        # 합산 순위에서 밀린 행은 같은 문서의 해당 표·셀을 가리키는 실제 키워드 검색 결과로 보충한다.
        def is_row(hit):
            return hit["payload"].get("kind") == "table_row" and not row_ids.isdisjoint(hit["payload"].get("cell_ids") or [])

        rows = [hit for hit in hits if is_row(hit)]
        if not rows:
            rows = [hit for hit in repository.search(label, whole_document, 30) if is_row(hit)]
        if rows:
            hits = (rows + [h for h in hits if not is_row(h)])[:12]
            outline["requested_row"] = label
    similarity = None
    embed = getattr(retriever, "embed", None)
    if embed is not None:
        # 후보 표의 행·열 라벨과 요구 문구를 한 번에 임베딩해 의미 유사도를 계산한다(캐시 재사용).
        from doc_understanding.rag.agentic.matching import similarity_from
        labels = [label for o in outlines for path in o["rows"] + o["columns"] for label in path.split(" > ")]
        similarity = similarity_from(embed, getattr(retriever, "profile_id", ""), items + labels)
    for rank, (rid, table_id) in enumerate(tables):
        for cell in structure_matches(docs[rid], table_id, items, scope, similarity):
            structured.append(dict(cell, run_id=rid, table_rank=rank))
    # 점수가 같으면 요구 문구와 글자가 똑같은 라벨을 가진 칸을 앞에 둔다. 글자 일치는 포함 관계도 인정하므로
    # 짧은 라벨이 긴 문구 안에 들어 있기만 해도 같은 점수가 된다(그런 칸이 검색 순위가 앞선 표에 많으면 정답 칸을 밀어낸다).
    def exact(cell):
        labels = {compact(label) for label in cell["row_path"] + cell["column_path"]}
        return sum(compact(term) in labels for term in cell["matched_terms"])

    structured.sort(key=lambda c: (-c["match_score"], -exact(c), c["table_rank"]))
    structured = structured[:max_structured]
    with span("agent.structure_match", dict(query_terms=items, scope=scope)) as box:
        box["output"] = dict(tables=outlines, structured=[
            dict(cell_id=c["cell_id"], table_id=c["table_id"], value_raw=c["value_raw"], row_path=c["row_path"],
                 column_path=c["column_path"], matched_terms=c["matched_terms"], match_score=c["match_score"],
                 scope_matched=c["scope_matched"]) for c in structured])
    context = resolve_evidence(hits, question, max_documents=max_documents,
                               max_cells_per_document=max_cells_per_document, sources=docs)
    evidence, seen = [], set()
    for e in [*structured, *context]:
        key = (e.get("run_id"), e.get("cell_id") or e.get("content_id") or e.get("chunk_id"))
        if key in seen:
            continue
        seen.add(key)
        evidence.append({k: v for k, v in e.items() if k not in ("matched_terms", "match_score", "table_rank",
                                                                  "scope_matched", "match_details")})
    for i, e in enumerate(evidence, 1):
        e["evidence_id"] = f"E{i}"
    trace = dict(queries=queries, hits=[dict(run_id=h["run_id"], entry_id=h["payload"]["entry_id"],
                                             kind=h["payload"].get("kind"), page=h["payload"].get("page"),
                                             cell_ids=h["payload"].get("cell_ids") or [],
                                             content_id=h["payload"].get("content_id"),
                                             content_ids=h["payload"].get("content_ids") or [])
                                        for h in hits],
                 tables=outlines,
                 structured=[dict(cell_id=c["cell_id"], table_id=c["table_id"], value_raw=c["value_raw"],
                                  matched_terms=c["matched_terms"], scope_matched=c["scope_matched"],
                                  match_score=c["match_score"],
                                  match_kinds=[(m["term"], m["kind"]) for m in c.get("match_details", [])])
                             for c in structured],
                 evidence_ids=[(e["evidence_id"], e.get("table_id"), e.get("cell_id") or e.get("content_id"))
                               for e in evidence])
    return evidence, trace


def unsupported_quotes(result):
    assessment = (result.get("assessment_validation") or {}).get("assessment") or {}
    return [r["question_quote"] for r in assessment.get("requirements", [])
            if r.get("status") != "supported" and r.get("question_quote")]


def answer(question, output, *, trace=None, **kwargs):
    """질문 이해 에이전트 실행. trace(runtime.telemetry.Trace)가 있으면 실행 전체를 하나의 Langfuse
    trace로 남기고 계획·임베딩·답변 모델 호출을 generation으로 기록한다. 추적 실패는 실행을 막지 않는다."""
    if trace is None:
        return _answer(question, output, **kwargs)
    with trace.request(question, "question_agent") as root:
        report = _answer(question, output, **kwargs)
        root["output"] = dict(answer=report["result"].get("answer"), retried=report["retried"],
                              plan_check=dict(reasons=report["plan_check"].get("reasons"),
                                              fallback=report["plan_check"].get("fallback")))
    trace.flush()
    report.update(trace_id=trace.trace_id, trace_warnings=list(trace.warnings))
    _write(Path(output) / "trace.json", dict(trace_id=trace.trace_id, warnings=trace.warnings))
    return report


def interleave(groups):
    return [item for row in zip_longest(*groups) for item in row if item is not None]


def number_evidence(items, trace):
    numbered = [dict(e, evidence_id=f"E{i}") for i, e in enumerate(items, 1)]
    trace["evidence_ids"] = [(e["evidence_id"], e.get("table_id"), e.get("cell_id") or e.get("content_id"))
                             for e in numbered]
    return numbered


def rerank_evidence(question, plan, evidence, trace, reranker, limit, output, *, targets=()):
    """리랭커로 근거 순서를 정하고 상위 limit개만 남긴 뒤 E1..로 다시 번호를 매긴다. 리랭커가 없으면 그대로."""
    if reranker is None and not targets:
        return evidence
    kept = evidence
    if reranker is not None:
        with span("agent.rerank", dict(question=question, candidates=len(evidence))) as box:
            kept, record = reranker.rerank(question, plan, evidence, None if targets else limit, output)
            trace["rerank"] = record
            box["output"] = dict(status=record.get("status"), candidates=len(kept))
    if targets:
        kept = interleave([[e for e in kept if e["run_id"] == rid] for rid in targets])[:limit]
        if reranker is not None:
            record.update(kept=len(kept), dropped=len(evidence) - len(kept))
    return number_evidence(kept, trace)


def _answer(question, output, *, run_id=None, retriever=None, plan_policy=None, allow_external=False,
            ledger=None, plan_transport=None, generate=generate_answer, max_documents=1, reranker=None,
            max_evidence=12, progress=None, plan_cache=None, directory=None, narrow=None, mentions=None,
            grid_loader=None, grid_shapes=None, grid_policy=None, explicit_scope=False, **answer_options):
    """progress(단계 이름): planning → searching → evidence → (reranking) → (reading) → answering, 재검색하면 한 번 더.
    reading은 근거가 속한 표를 통째로 읽을 때만 나온다(계획이 그렇게 정했거나, 보류 뒤 한 번 넓힐 때).
    문서 고르기: directory()가 문서 목록(메타데이터 포함)을 주면 질문의 대상을 문서와 맞춘다(selecting 단계).
    narrow(run_ids)는 그 문서들로 좁힌 검색기를, mentions(낱말[, run_id])는 본문에 그 낱말이 나오는지를 준다.
    directory가 없으면(사용자가 문서를 직접 골랐거나 단일 문서 질문) 고르지 않는다.
    원문 격자 읽기: grid_loader(run_id, table_id)가 표의 물리 격자를, grid_shapes(run_id)가 문서의 표 목록을, grid_policy가
    읽기에 쓸 모델·단가를 주면, 보류로 끝난 질문에서 구조가 확정되지 않은 표를 한 번 읽힌다(grid 단계). 셋 중 loader나 policy가
    없으면 하지 않는다.
    실제로 실행하는 단계만 알린다(리랭커가 없으면 reranking은 없다)."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    retriever = retriever or repository.search
    report = progress or (lambda stage: None)

    if plan_policy is not None and ledger is None:
        ledger = Ledger(max_calls=1, max_total_usd=plan_policy.max_cost_usd)

    def plan_question(state):
        report("planning")
        with span("agent.plan", dict(question=question)) as box:
            if plan_policy is None:
                plan, plan_check = fallback_plan(question), dict(passed=False, reasons=["PLANNER_DISABLED"],
                                                                 fallback=True)
            else:
                plan, plan_check = make_plan(question, plan_policy, output / "plan", allow_external=allow_external,
                                             ledger=ledger, transport=plan_transport, cache=plan_cache)
            box["output"] = dict(requirements=[dict(question_quote=r.question_quote, requirement=r.text, role=r.role)
                                               for r in plan.requirements], queries=plan.queries,
                                 plan_check=dict(reasons=plan_check.get("reasons"), fallback=plan_check.get("fallback")))
        if plan.answer_request is not None:
            answer_options['answer_request'] = plan.answer_request.model_dump()
        return dict(plan=plan, plan_check=plan_check)

    scope = dict(retriever=retriever, max_documents=max_documents, known=[], targets=[])

    def select_documents(state):
        plan = state["plan"]
        if plan.ambiguity:
            ask = plan.ambiguity
            text = ask.follow_up.strip()
            if directory is not None and not plan.subjects and not run_id:
                available = directory()
                if len(available) > 1:
                    names = []
                    if len(available) <= 3:
                        for doc in available:
                            title = doc.get("title") or doc.get("name") or ""
                            issuer = doc.get("organization") or ""
                            names.append(title if issuer in title else f"{issuer} {title}".strip())
                    choices = " · ".join(dict.fromkeys(n for n in names if n))
                    text = (f"{choices} 중 어느 문서를 기준으로 볼까요? " if choices
                            else "어느 문서를 기준으로 볼까요? ") + text
            result = dict(question=question, answer=text, answer_text=text, abstained=True, model_calls=0,
                          selection=dict(evidence_ids=[], values_raw=[], abstained=True), facts_published=0,
                          clarification=dict(kind="needs_context", question_quote=ask.question_quote,
                                             readings=ask.readings))
            return dict(documents=None, result=result, evidence=[], attempts=[], answer_calls=[], stop=True)
        if directory is None or (explicit_scope and len(plan.subjects) < 2):
            return dict(documents=None)
        report("selecting")
        with span("agent.select_documents", dict(question=question)) as box:
            from doc_understanding.rag.agentic.publications import equivalent
            scope["known"] = directory()
            chosen = document_selection.select(state["plan"].subjects, scope["known"], mentions,
                                               question=question, equivalent=equivalent)
            box["output"] = dict(status=chosen["status"], run_ids=chosen["run_ids"],
                                 subjects=[dict(subject=s["subject"], status=s["status"]) for s in chosen["subjects"]])
        if chosen["status"] in ("needs_choice", "not_found"):
            # 되묻기·문서 없음: 검색도 답변도 하지 않는다. 화면이 후보나 비슷한 문서를 보여 준다.
            text = ("질문이 가리킨 문서를 하나로 정하지 못했습니다. 아래에서 문서를 골라 주세요."
                    if chosen["status"] == "needs_choice" else "질문이 가리킨 대상의 문서가 없습니다.")
            result = dict(question=question, answer=text, abstained=True, model_calls=0, facts_published=0,
                          selection=dict(evidence_ids=[], values_raw=[], abstained=True),
                          clarification=dict(kind=chosen["status"], subject=chosen.get("subject"),
                                             documents=chosen["candidates"] or chosen["similar"]))
            return dict(documents=chosen, result=result, evidence=[], attempts=[], answer_calls=[], stop=True)
        if chosen["status"] == "selected" and narrow is not None:
            ids = chosen["run_ids"]
            if len(ids) > 1 and all(len(s["documents"]) == 1 for s in chosen["subjects"]):
                scope["targets"] = ids
                answer_options["comparison_quotes"] = [r.question_quote for r in state["plan"].requirements]
            else:
                scope.update(retriever=narrow(ids), max_documents=max(1, min(len(ids), 3)),
                             whole_document=ids[0] if len(ids) == 1 else None)
        return dict(documents=chosen)

    def gather_evidence(state):
        # 재검색은 미충족 요구에 대한 새 검색어만 실행한다. 원래 검색어는 반복하지 않는다.
        retrying = bool(state.get('attempts'))
        previous = state.get('evidence') or []
        if scope["targets"]:
            batches, traces = [], []
            for rid in scope["targets"]:
                batch, track = gather(question, state["plan"], rid, narrow([rid]), whole_document=rid,
                                      extra_queries=state.get("retry_quotes") or (), progress=progress, retry_only=retrying)
                batches.append(batch)
                traces.append(track)
            track = dict(queries=traces[0]["queries"], comparison_run_ids=scope["targets"])
            for key in ("hits", "tables", "structured"):
                track[key] = interleave([t[key] for t in traces])
            return dict(evidence=number_evidence(interleave(batches), track), trace=track, retry_base=previous)
        found, track = gather(question, state["plan"], run_id, scope["retriever"],
                              extra_queries=state.get("retry_quotes") or (), max_documents=scope["max_documents"],
                              progress=progress, whole_document=scope.get("whole_document"), retry_only=retrying)
        return dict(evidence=found, trace=track, retry_base=previous)

    def select_evidence(state):
        found, track, retrying = state["evidence"], state["trace"], bool(state.get("attempts"))
        attempt = dict(trace=track, retry_quotes=state["retry_quotes"]) if retrying else dict(trace=track)
        attempts = [*(state.get("attempts") or []), attempt]
        previous = state.get('retry_base') or []
        def stop_retry(reason):
            track['retry'] = dict(reason=reason,answer_called=False)
            track['evidence_ids']=[(e['evidence_id'],e.get('table_id'),e.get('cell_id') or e.get('content_id')) for e in previous]
            return dict(evidence=previous,trace=track,attempts=attempts,skip_answer=True,
                        widen=False,retry_quotes=[])
        if retrying:
            if not recovery.atoms(found)-recovery.atoms(previous):
                return stop_retry('NO_NEW_RETRIEVED_EVIDENCE')
            found=number_evidence(recovery.merge(previous,found),track)
        named = [t for t in track.get("tables", []) if t.get("requested_row")]
        if (grid_loader is not None and grid_policy is not None and scope["targets"]
                and len(named) <= grid_reading.MAX_TABLES
                and {t["run_id"] for t in named} == set(scope["targets"])
                and len({t["requested_row"] for t in named}) == 1):
            # 두 문서에서 같은 행을 찾았지만 두 표 모두 구조 미확정이다. 다른 표로 대신 답하지 않는다.
            track["required_grid_rows"] = {t["table_id"]: t["requested_row"] for t in named}
            track["evidence_ids"] = []
            return dict(evidence=[], trace=track, attempts=attempts, answer_calls=state.get("answer_calls") or [],
                        grid=[(t["run_id"], t["table_id"]) for t in named],
                        result=dict(question=question, abstained=True, model_calls=0, facts_published=0,
                                    answer="질문에 해당하는 표의 원문 행과 열을 확인하지 못했습니다."))
        if reranker is not None and found:
            report("reranking")
        ranking_question = question + '\nMissing evidence: ' + ' / '.join(state['retry_quotes']) if retrying else question
        kept = rerank_evidence(ranking_question, state["plan"], found, track, reranker, max_evidence,
                               output / ("rerank-retry" if retrying else "rerank"), targets=scope["targets"])
        if state['plan'].answer_request is not None:
            from doc_understanding.rag.evidence.adjacent import expand
            from doc_understanding.rag.evidence.headings import attach
            before = len(kept)
            kept = number_evidence(attach(expand(kept)), track)
            track['adjacent_narrative'] = dict(before=before, after=len(kept))
        if retrying:
            fresh=recovery.atoms(kept)-recovery.atoms(previous)
            if not fresh:
                return stop_retry('NO_NEW_SELECTED_EVIDENCE')
            ranking=track.get('rerank') or {}
            if ranking.get('status')!='completed':
                return stop_retry('RETRY_RELEVANCE_UNCONFIRMED')
            relevant={s['evidence_id'] for s in ranking.get('scores',[]) if (s.get('relevance') or 0)>=2}
            if not recovery.atoms([e for e in found if e['evidence_id'] in relevant])-recovery.atoms(previous):
                return stop_retry('NEW_EVIDENCE_NOT_RELEVANT_TO_GAP')
            track['retry']=dict(reason='NEW_EVIDENCE_FOR_GAP',answer_called=True,new_source_parts=len(fresh))
        return dict(evidence=kept, attempts=attempts, expanded=False, focused=None,
                    widen=False, skip_answer=False)

    def expand_evidence(state):
        # 근거가 속한 표를 통째로 근거에 넣는다. 답한 뒤에 들어왔으면(보류 뒤 넓히기) 더해진 것이 있을 때만 다시 답한다.
        after_answer_call = bool(state.get("widen"))
        report("reading")
        with span("agent.expand", dict(evidence=len(state["evidence"]))) as box:
            wide, record = expand_units(state["evidence"])
            box["output"] = record
        state["trace"]["reading"] = dict(record, after_abstention=after_answer_call)
        added = bool(recovery.atoms(wide)-recovery.atoms(state['evidence']))
        if not added:
            return dict(expanded=True, widen=False, widened=state.get("widened") or after_answer_call,
                        skip_answer=after_answer_call)
        state["trace"]["evidence_ids"] = [(e["evidence_id"], e.get("table_id"), e.get("cell_id") or e.get("content_id"))
                                          for e in wide]
        return dict(evidence=wide, focused=state["evidence"], expanded=True, widen=False, skip_answer=False,
                    widened=state.get("widened") or after_answer_call)

    def abstained(result):
        return bool((result.get("selection") or {}).get("abstained", result.get("abstained", False)))

    def generate_answer_step(state):
        found, retrying = state["evidence"], len(state["attempts"]) > 1
        folder = ("answer-retry" if retrying else "answer") + ("-whole" if state.get("answer_calls") and state.get("expanded")
                                                               and len(state["answer_calls"]) >= len(state["attempts"]) else "")
        if found or retrying:
            report("answering")
            try:
                result = generate(question, found, output / folder, allow_external=allow_external, verify_sql=True,
                                  **answer_options)
            except RuntimeError:
                # 보류 뒤에 표 전체로 넓혀 한 번 더 답해 보는 호출이 실패했다. 덤으로 해 본 시도이므로 질문 전체를 오류로
                # 만들지 않고 앞의 보류를 그대로 둔다(실패 기록은 그 호출 폴더의 failure.json에 남는다).
                if not (folder.endswith("-whole") and state.get("result") is not None):
                    raise
                state["trace"]["reading"].update(used=False, reason="ANSWER_CALL_FAILED")
                result = dict(state["result"], model_calls=1)
                # 앞의 결과는 넓히기 전 근거 번호로 만든 것이다. 근거와 기록의 번호도 그때 것으로 되돌린다.
                if state.get("focused"):
                    found = state["focused"]
                    state["trace"]["evidence_ids"] = [(e["evidence_id"], e.get("table_id"),
                                                       e.get("cell_id") or e.get("content_id")) for e in found]
            except ValueError as error:
                # 표 전체가 한 번의 요청 한도를 넘으면 넓히기 전 근거로 답한다(넓힌 근거를 잘라 쓰지 않는다).
                if str(error) != "MODEL_REQUEST_BUDGET_EXCEEDED" or not state.get("focused"):
                    raise
                state["trace"]["reading"]["used"] = False
                state["trace"]["reading"]["reason"] = "REQUEST_TOO_LARGE"
                found = state["focused"]
                # 기록의 근거 번호도 실제로 쓴 근거(넓히기 전)로 되돌린다.
                state["trace"]["evidence_ids"] = [(e["evidence_id"], e.get("table_id"), e.get("cell_id") or e.get("content_id"))
                                                  for e in found]
                if state.get('result') is not None:
                    result = dict(state['result'],model_calls=0)
                else:
                    result = generate(question, found, output / (folder + "-focused"), allow_external=allow_external,
                                      verify_sql=True, **answer_options)
            # 표 전체를 읽어야 하는 질문인데 전체를 읽지 못했으면 결과에 남긴다(화면이 '일부만 답했을 수 있음'을 알린다).
            reading = state["trace"].get("reading")
            if getattr(state["plan"], "reading", "focused") == "whole" and reading:
                skipped = [t["reason"] for t in reading["tables"] if not t["expanded"]]
                withheld = sum(t.get("withheld") or 0 for t in reading["tables"] if t["expanded"])
                if reading.get("used") is False or skipped or withheld:
                    result["reading_incomplete"] = dict(reason=reading.get("reason") or (skipped[0] if skipped else "CELLS_WITHHELD"),
                                                        tables=len(skipped), withheld_cells=withheld)
        else:
            result = dict(question=question, answer="검색된 근거가 없습니다.", abstained=True,
                          model_calls=0, facts_published=0)
        chosen = state.get("documents") or {}
        if (chosen.get("status") == "unconfirmed" or scope["targets"]) and not abstained(result):
            used = set((result.get("selection") or {}).get("evidence_ids") or [])
            cited = [e.get("run_id") for e in found if e["evidence_id"] in used]
            passed, missing = document_selection.check_citations(chosen, cited, scope["known"], mentions)
            result["subject_check"] = dict(passed=passed, missing=missing)
            if not passed:
                result.update(selection=dict(evidence_ids=[], values_raw=[], abstained=True), abstained=True,
                              answer_text="", answer_text_source="none",
                              answer="질문이 가리킨 대상을 언급하는 문서에서 근거를 찾지 못했습니다: " + ", ".join(missing),
                              alternatives=[], reasons=["MISSING_COMPARISON_CITATION" if scope["targets"]
                                                        else "CITED_DOCUMENT_DOES_NOT_MENTION_SUBJECT"])
        # 근거 부족으로 판정된 질문 구간으로 한 번만 다시 검색한다(분기는 graph.after_answer). 근거가 없으면 다시 찾지 않는다.
        # 보류였고 아직 넓혀 보지 않았으면 표 전체로 넓혀 한 번 더 답한다(질문 한 건에 한 번).
        recovery_request = recovery.request(question,state['plan'],result,
                                             [q for a in state['attempts'] for q in a['trace'].get('queries',[])])
        state['trace']['recovery'] = recovery_request
        widen = (abstained(result) and recovery_request['local_gap'] and bool(found)
                 and not state.get("expanded") and not state.get("widened")
                 and any(e.get("table_id") for e in found))
        # 보류로 끝났고, 검색에 걸린 표 가운데 쓸 수 있는 칸이 하나도 없는 표가 있으면 그 표를 원문 격자로 읽어 본다.
        unread = []
        if abstained(result) and grid_loader is not None and grid_policy is not None:
            unread = [(t["run_id"], t["table_id"]) for t in state["trace"].get("tables") or []
                      if t.get("run_id") and t.get("values") and not t.get("eligible")]
        if result.get("interval_tables"):
            unread = result["interval_tables"] if grid_loader is not None and grid_policy is not None else []
            widen = False
        if recovery_request['reason'] in ('EXTERNAL_VERIFICATION_REQUIRED','VALIDATION_NOT_RETRIEVAL_FAILURE'):
            unread = []
        # A rejected metric comparison does not authorize answering from an unrelated unreadable table.
        if {"REQUESTED_AXIS_NOT_CITED", "COMPARISON_DURATION_MISSING", "REQUESTED_INTERVAL_NOT_CITED"}.intersection(
                (result.get("assessment_validation") or {}).get("reasons", [])):
            unread = []
        return dict(result=result, evidence=found, widen=widen, grid=unread,
                    answer_calls=[*(state.get("answer_calls") or []), result.get("model_calls", 0)],
                    retry_quotes=recovery_request['queries']
                    if abstained(result) and found and not retrying else [])

    def read_grid(state):
        report("grid")
        trace = state["trace"]
        names = {d["run_id"]: " | ".join(x for x in (d.get("organization"), d.get("title") or d.get("name"), d.get("as_of")) if x)
                 for d in scope["known"]}
        try:
            with span("agent.read_grid", dict(question=question, tables=len(state["grid"]))) as box:
                verdict, cells, record = grid_reading.read_tables(
                    question, state["grid"], grid_loader, grid_shapes, grid_policy, output / "grid",
                    interval_required=bool((state.get("result") or {}).get("interval_tables")),
                    allow_external=allow_external, describe_document=names.get,
                    terms=[r.text for r in state["plan"].requirements],
                    # 요구 사항이 인용 라벨에 닿는지는 문서 이름을 알 때만 본다. 이름을 모르면 '문서 이름을 말한 요구 사항'을
                    # 가려낼 수 없어 맞는 답까지 막는다(사용자가 문서를 직접 고른 질문).
                    check_requirements=bool(names), subjects=list(state["plan"].subjects),
                    required_rows=trace.get("required_grid_rows"), requirements=state["plan"].requirements)
                box["output"] = dict(status=verdict["status"], reasons=verdict.get("reasons"))
        except Exception as error:
            # 덤으로 해 보는 시도다. 실패해도 앞의 보류를 그대로 둔다.
            trace["grid"] = dict(status="failed", error=type(error).__name__)
            return dict(grid=[])
        call = record.pop("call", None) or {}
        grid_calls = 0 if record.get("source_interval") else 1
        trace["grid"] = dict(record, status=verdict["status"], reasons=verdict.get("reasons"),
                             estimated_cost_usd=call.get("estimated_cost_usd"))
        if verdict["status"] != "answered":
            return dict(grid=[])
        chosen = state.get("documents") or {}
        result = dict(question=question, answer=verdict["answer_text"], answer_text=verdict["answer_text"],
                      answer_text_source=verdict.get("answer_text_source", "model"), answer_text_reasons=[], alternatives=[],
                      selection=dict(evidence_ids=[e["evidence_id"] for e in cells], values_raw=[e["value_raw"] for e in cells],
                                     abstained=False),
                      model=call.get("model"), estimated_cost_usd=call.get("estimated_cost_usd") or 0.0, model_calls=grid_calls,
                      generation_mode="raw_grid_reading", semantic_status="unvalidated_candidate", facts_published=0,
                      grid_reading=dict(tables=[t for t in record["tables"] if t.get("read")]))
        if chosen.get("status") == "unconfirmed" or scope["targets"]:
            passed, missing = document_selection.check_citations(chosen, [e["run_id"] for e in cells], scope["known"], mentions)
            result["subject_check"] = dict(passed=passed, missing=missing)
            if not passed:
                trace["grid"]["status"] = "rejected"
                trace["grid"]["reasons"] = ["MISSING_COMPARISON_CITATION" if scope["targets"]
                                            else "CITED_DOCUMENT_DOES_NOT_MENTION_SUBJECT"]
                return dict(grid=[])
        trace["evidence_ids"] = [(e["evidence_id"], e.get("table_id"), e.get("cell_id")) for e in cells]
        return dict(result=result, evidence=cells, grid=[],
                    answer_calls=[*(state.get("answer_calls") or []), grid_calls])

    from doc_understanding.rag.agentic.graph import build
    state = build(dict(plan_question=plan_question, select_documents=select_documents,
                       gather_evidence=gather_evidence, select_evidence=select_evidence,
                       expand_evidence=expand_evidence, generate_answer=generate_answer_step,
                       read_grid=read_grid)).invoke(dict(question=question))
    plan, plan_check, attempts = state["plan"], state["plan_check"], state["attempts"]
    result, evidence, answer_calls = state["result"], state["evidence"], state["answer_calls"]
    retry = len(attempts) > 1
    report = dict(question=question, plan=plan.model_dump(), plan_check=plan_check, attempts=attempts,
                  retried=bool(retry), result=result, evidence=evidence, documents=state.get("documents"),
                  plan_model_calls=len(ledger.calls) if ledger else 0,
                  plan_estimated_cost_usd=ledger.estimated_usd if ledger else 0.0,
                  answer_model_calls=sum(answer_calls),
                  rerank_estimated_cost_usd=sum(a["trace"].get("rerank", {}).get("estimated_cost_usd") or 0
                                                for a in attempts),
                  grid_estimated_cost_usd=sum((a["trace"].get("grid") or {}).get("estimated_cost_usd") or 0
                                              for a in attempts),
                  semantic_status="unvalidated_candidate")
    _write(output / "agent-run.json", report)
    return report
