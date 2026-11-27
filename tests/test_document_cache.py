"""최근 문서 기억: 같은 실행·같은 스냅샷이면 다시 읽지 않고, 내용이 바뀌면 다시 읽는다."""
from doc_understanding.rag.retrieval import repository


def test_fetch_document_reuses_recent_documents_until_the_snapshot_changes(monkeypatch):
    state = {"sha": "s1", "payload_reads": 0}

    def query(sql):
        if sql.startswith("SELECT snapshot_sha256"):
            return [{"snapshot_sha256": state["sha"], "artifact_id": "a"}]
        state["payload_reads"] += 1
        return [{"payload": {"n": state["payload_reads"]}}]

    monkeypatch.setattr(repository, "query", query)
    monkeypatch.setattr(repository.CanonicalDocument, "model_validate", classmethod(lambda cls, payload: dict(payload)))
    monkeypatch.setattr(repository, "validate_document", lambda doc: None)
    monkeypatch.setattr(repository, "_documents", repository.OrderedDict())
    fetch = getattr(repository.fetch_document, "__wrapped__", repository.fetch_document)

    first = fetch("run-1")
    assert fetch("run-1") is first and state["payload_reads"] == 1
    state["sha"] = "s2"  # 같은 실행 ID의 내용이 달라지면 다시 읽는다
    assert fetch("run-1") is not first and state["payload_reads"] == 2
    for i in range(repository.DOCUMENT_CACHE_SIZE + 1):
        fetch(f"other-{i}")
    assert len(repository._documents) == repository.DOCUMENT_CACHE_SIZE
    fetch("run-1")
    assert state["payload_reads"] == 2 + repository.DOCUMENT_CACHE_SIZE + 2  # 밀려난 문서는 다시 읽는다
