"""근거 저장본: 문서에서 바로 계산한 것과 저장해 두고 ID로 읽은 것이 같아야 한다."""
import json
from pathlib import Path

import pytest

from doc_understanding.rag.evidence.source import (
    CONTENT, TABLE, DocumentSource, StoredSource, as_source, evidence_items,
)

ROOT = Path(__file__).resolve().parents[1]


def assembled():
    from tests.test_document_rag import assembled as build
    return build()


def stored_from(doc, calls=None):
    """evidence_items를 JSON으로 왕복시킨 저장소(DB의 jsonb와 같은 조건)."""
    from doc_understanding.rag.indexing.entries import make_entries
    rows = {(kind, item_id): json.dumps(payload, ensure_ascii=False) for kind, item_id, payload in evidence_items(doc)}
    rows.update({("entry", e["entry_id"]): json.dumps(e, ensure_ascii=False) for e in make_entries(doc)})

    def load(kind, ids):
        if calls is not None:
            calls.append((kind, list(ids)))
        return {i: json.loads(rows[(kind, i)]) for i in ids if (kind, i) in rows}

    return StoredSource(dict(document_id=doc.document_id), load)


def test_stored_items_equal_what_the_document_computes():
    doc = assembled()
    direct, stored = DocumentSource(doc), stored_from(doc)
    assert stored.document_id == direct.document_id == doc.document_id
    for st in doc.table_structure:
        assert stored.table(st.table_id) == direct.table(st.table_id)
        assert stored.cells(st.table_id) == direct.cells(st.table_id)
        assert stored.nested_context(st.table_id) == direct.nested_context(st.table_id)
    ids = list(direct.contents())
    assert ids and stored.content_items(ids) == direct.content_items(ids)
    assert [i["position"] for i in stored.content_items(list(reversed(ids)))] == sorted(
        i["position"] for i in direct.contents().values())
    assert stored.content("missing") is None and direct.content("missing") is None
    with pytest.raises(ValueError, match="EVIDENCE_STORE_TABLE_NOT_FOUND"):
        stored.table("missing")


def test_stored_source_reads_each_item_once_and_hands_out_copies():
    doc = assembled()
    calls = []
    stored = stored_from(doc, calls)
    table_id = doc.table_structure[0].table_id
    first = stored.cells(table_id)
    first[0]["evidence_id"] = "E1"  # 호출자가 근거에 덧붙여도 저장본은 바뀌지 않는다
    assert "evidence_id" not in stored.cells(table_id)[0]
    assert calls == [(TABLE, [table_id])]
    ids = list(DocumentSource(doc).contents())[:3]
    stored.preload(content_ids=ids)
    stored.content_items(ids)
    assert calls[1:] == [(CONTENT, ids)]


def test_as_source_wraps_documents_and_keeps_sources():
    doc = assembled()
    source = as_source(doc)
    assert isinstance(source, DocumentSource) and as_source(source) is source
    stored = stored_from(doc)
    assert as_source(stored) is stored


def test_index_writes_evidence_items_in_the_same_transaction(monkeypatch):
    from doc_understanding.rag.indexing import writer

    doc = assembled()
    scripts = []
    monkeypatch.setattr(writer, "query", lambda statement: [])
    monkeypatch.setattr(writer, "sql", scripts.append)
    writer.create_index("run-1", doc, "0" * 64)
    script = scripts[0]
    assert script.startswith("BEGIN;") and script.endswith("COMMIT;")
    assert script.count("INSERT INTO rag.evidence_item") == len(evidence_items(doc))
    assert script.count("INSERT INTO rag.search_entry") == len(writer.make_entries(doc))
    # 같은 버전으로 이미 색인된 실행: 검색 항목과 저장본 수가 모두 맞아야 한다.
    counts = {"rag.search_entry": len(writer.make_entries(doc)), "rag.evidence_item": 0}

    def existing(statement):
        if "document_snapshot" in statement:
            return [{"snapshot_sha256": "0" * 64, "index_version": writer.INDEX_VERSION}]
        return [{"n": next(n for table, n in counts.items() if table in statement)}]

    monkeypatch.setattr(writer, "query", existing)
    with pytest.raises(ValueError, match="INDEX_COVERAGE_MISMATCH"):
        writer.create_index("run-1", doc, "0" * 64)
    counts["rag.evidence_item"] = len(evidence_items(doc))
    assert writer.create_index("run-1", doc, "0" * 64) is None and len(scripts) == 1


def test_stored_source_rejects_search_pieces_that_differ_from_the_indexed_ones():
    from doc_understanding.rag.evidence.pack import evidence_pack
    from doc_understanding.rag.indexing.entries import make_entries

    doc = assembled()
    body = next(e for e in make_entries(doc) if e.get("content_ids") and not e.get("table_id"))
    hit = json.loads(json.dumps(body, ensure_ascii=False))
    assert evidence_pack(stored_from(doc), [hit], "q") == evidence_pack(doc, [hit], "q")
    for change in (dict(primary_text="바뀐 문구"), dict(content_ids=[]), dict(entry_id="unknown/text/0")):
        altered = dict(hit, **change)
        for source in (stored_from(doc), doc):
            with pytest.raises(ValueError, match="SEARCH_PROJECTION_SOURCE_MISMATCH"):
                evidence_pack(source, [altered], "q")
