# 역할: 변경된 검색 텍스트만 배치 임베딩하고 완료된 배치부터 재사용 가능하게 저장한다.
from doc_understanding.rag.embedding import repository
from doc_understanding.rag.embedding.models import input_hash


def embed_run(run_id, client, *, progress=None):
    cfg = client.config
    entries = repository.load_entries(run_id)
    # 같은 문서를 다시 적재하거나 문서끼리 같은 문구가 있으면, 이미 만든 벡터를 복사해 중복 임베딩을 막는다.
    copied = repository.copy_identical(run_id, cfg)
    cached = repository.cached_hashes(run_id, cfg)
    pending = {}
    skipped, reused = [], 0
    for entry in entries:
        text = entry["text"]
        if not text.strip() or len(text.encode("utf-8")) > cfg.max_input_bytes:
            skipped.append(dict(entry_id=entry["entry_id"], reason="EMPTY_OR_OVERSIZED_INPUT"))
            continue
        digest = input_hash(text)
        if cached.get(entry["entry_id"]) == digest:
            reused += 1
        else:
            pending.setdefault(digest, []).append(entry)
    groups = list(pending.values())
    written = 0
    # 웹 실행기는 확정 저장된 배치만 진행률에 반영한다. CLI 호출 방식은 유지한다.
    def report():
        if progress:
            progress(dict(total=len(entries), reused=reused, written=written, skipped=skipped))

    report()
    # 입력 길이 한도는 모델마다 다르다. 서버가 입력을 거부하면(400·413·422) 묶음을 하나씩 다시 보내
    # 거부된 항목만 건너뛴다. 그 밖의 실패(시간 초과·서버 오류·인증)는 그대로 실패한다.
    def embed_each(batch):
        pairs = []
        for group in batch:
            try:
                pairs.append((group, client.embed([group[0]["text"]])[0]))
            except RuntimeError:
                if not client.input_rejected:
                    raise
                skipped.extend(dict(entry_id=e["entry_id"], reason="PROVIDER_REJECTED_INPUT") for e in group)
        return pairs

    for start in range(0, len(groups), cfg.batch_size):
        batch = groups[start : start + cfg.batch_size]
        try:
            pairs = list(zip(batch, client.embed([group[0]["text"] for group in batch]), strict=True))
        except RuntimeError:
            if not client.input_rejected:
                raise
            pairs = embed_each(batch)
        items = [{**entry, "vector": vector} for group, vector in pairs for entry in group]
        if items:
            repository.save_batch(run_id, cfg, items)
        written += len(items)
        report()
    if groups and not written and not reused and any(s["reason"] == "PROVIDER_REJECTED_INPUT" for s in skipped):
        # 하나도 받아들여지지 않았으면 입력이 아니라 설정(모델 이름·차원 등) 문제다. 건너뛴 것으로 끝내지 않는다.
        raise RuntimeError("EMBEDDING_CALL_FAILED_SEE_AUDIT")
    return dict(
        run_id=run_id,
        profile=cfg.profile,
        profile_id=cfg.profile_id,
        total=len(entries),
        reused=reused,
        copied_from_other_runs=copied,
        written=written,
        skipped=skipped,
        calls=client.audit,
        coverage_complete=not skipped and reused + written == len(entries),
    )
