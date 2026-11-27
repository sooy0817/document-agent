# 역할: RAG 채점에서 틀린 문항을 한 건씩 기록으로 만든다(질문·기대·실제 답, 어느 단계에서 놓쳤는지).
"""실패 장부(질문). 저장된 실행 결과와 채점 결과만 읽는다.

실패 단계
- retrieval: 정답이 검색 후보에 없다
- evidence: 검색 후보에는 있는데 답변에 넘긴 근거에 없다(근거 복원·사용 가능 여부·리랭커)
- selection: 근거에는 있는데 1위가 아니고 답이 틀렸다(다른 근거를 골랐다)
- answer: 정답 근거가 1위인데 답이 틀렸거나 보류했다
- abstention: 답이 없는 질문에 답했다
- answer_error: 답변까지 실행하는 평가에서 실행이 실패했다(답변 결과 없음)
- run_error: 검색만 실행하는 평가에서 실행이 실패했다(답변 실패로 세지 않는다)
- ambiguity: 모호한 질문인데 보류하지도 다른 해석을 함께 내지도 않았다
"""


def item_failure(item, row, run, config, set_name):
    result = run.get("result")
    selection = (result or {}).get("selection") or {}
    first, rank = row.get("first_gold_rank"), row.get("evidence_first_rank")
    stage = None
    from doc_understanding.evaluation.rag.metrics import mode_of
    mode = row.get("mode") or mode_of(config, [run])
    if run.get("error") or (mode == "answer" and result is None):
        stage = "answer_error" if mode == "answer" else "run_error"
    elif result is not None and not item.answerable:
        stage = None if row.get("abstain_correct") else "abstention"
    elif result is not None and item.ambiguous:
        stage = None if row.get("ambiguity_flagged") else "ambiguity"
    elif result is not None and item.answerable:
        if row.get("answer_string_match") and row.get("selected_all_required_evidence") is False:
            stage = "selection" if row.get("all_required_evidence") else "evidence"
        elif not row.get("answer_correct"):
            stage = ("retrieval" if item.gold and first is None and rank is None else
                     "evidence" if item.gold and rank is None else
                     "selection" if item.gold and rank > 1 else "answer")
    elif result is None and item.gold:
        stage = "retrieval" if first is None and rank is None else "evidence" if rank is None else None
    if stage is None:
        return None
    return dict(set=set_name, config=config, id=item.id, type=item.type, question=item.question, stage=stage,
                expected=list(item.expected), gold=[a.model_dump(exclude_none=True) for a in item.gold],
                answer_text=" ".join(((result or {}).get("answer_text") or "").split())[:400],
                selected=[" ".join((v or "").split())[:160] for v in selection.get("values_raw") or []][:5],
                missing_selected_gold_docs=row.get("missing_selected_gold_docs"),
                abstained=row.get("abstained"), first_gold_rank=first, evidence_first_rank=rank,
                error=run.get("error_message") or run.get("error"), note=item.note,
                top_evidence=[dict(source=e.get("source_name"), page=e.get("page"),
                                   row_path=[" ".join(p.split()) for p in e.get("row_path") or []],
                                   column_path=[" ".join(p.split()) for p in e.get("column_path") or []],
                                   text=" ".join((e.get("value_raw") or "").split())[:120])
                              for e in (run.get("evidence") or [])[:3]])


def summarize(failures):
    by = {}
    for f in failures:
        by.setdefault(f["config"], {}).setdefault(f["stage"], []).append(f["id"])
    return by
