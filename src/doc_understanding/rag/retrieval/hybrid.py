# 역할: 점수 척도가 다른 키워드·벡터 검색 결과를 순위 기반 RRF로 결합한다.
def fuse(lexical, dense, limit=8, rank_constant=60):
    if type(rank_constant) is not int or rank_constant < 1:
        raise ValueError("INVALID_RRF_CONSTANT")
    combined = {}
    for method, hits in (("lexical", lexical), ("dense", dense)):
        seen = set()
        for hit in hits:
            key = (hit["run_id"], hit["payload"]["entry_id"])
            if key in seen:
                continue
            seen.add(key)
            rank = len(seen)
            if key not in combined:
                combined[key] = dict(
                    run_id=hit["run_id"],
                    payload=hit["payload"],
                    score=0.0,
                    retrieval=dict(method="rrf", ranks={}, source_scores={}),
                )
            elif combined[key]["payload"] != hit["payload"]:
                raise ValueError("SEARCH_SNAPSHOT_CHANGED")
            result = combined[key]
            result["score"] += 1.0 / (rank_constant + rank)
            result["retrieval"]["ranks"][method] = rank
            result["retrieval"]["source_scores"][method] = hit["score"]
    ordered = sorted(
        combined.values(), key=lambda h: (-h["score"], h["run_id"], h["payload"]["entry_id"])
    )
    return ordered[: max(1, min(int(limit), 30))]
