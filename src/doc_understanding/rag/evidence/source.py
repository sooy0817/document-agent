# 역할: 근거 복원이 문서에서 읽는 것(표의 셀·담긴 위치·본문)을 한 가지 인터페이스로 묶는다. 문서 전체에서 바로
# 계산하는 구현과, 적재 때 저장해 둔 항목을 ID로 읽는 구현이 같은 결과를 낸다.
"""근거 출처(EvidenceSource).

질문 때 필요한 것은 검색에 걸린 표 몇 개와 본문 몇 개뿐인데, 예전에는 그때마다 문서 전체(수 MB)를 DB에서 읽어 검증하고
표의 셀·칸 안 내용을 다시 계산했다. 이 계산은 문서가 정해지면 결과가 정해지고 질문과 무관하므로, 색인을 만들 때 표·본문
단위로 저장해 두고(`evidence_items`) 질문 때는 ID로 읽는다(`StoredSource`).

- DocumentSource: CanonicalDocument에서 바로 계산한다(적재, 화면 점검, 평가, 테스트).
- StoredSource: rag.evidence_item에 저장된 항목을 읽는다(질문 경로). 저장본은 검색 항목과 같은 트랜잭션에서 같은 문서로
  만들어지고 색인 버전이 같을 때만 쓴다. 다르면 다시 적재해야 한다(EVIDENCE_STORE_STALE).

두 구현의 결과가 같다는 것은 테스트와 `scripts/check_evidence_store.py`가 코퍼스 전체에서 비교한다.
저장본은 원문·경로 후보를 옮겨 둔 것이며 사람 검증을 거친 사실이 아니다.
"""
import copy
import json

TABLE, CONTENT, ENTRY = "table", "content", "entry"  # entry는 rag.search_entry에서 읽는다(저장본에 따로 두지 않는다)


class DocumentSource:
    """문서 전체에서 바로 계산한다."""

    def __init__(self, doc):
        self.doc = doc
        self.document_id = doc.document_id
        self._contents = None

    def _nested(self):
        from doc_understanding.rag.indexing.nested import nested_projection
        return nested_projection(self.doc)

    def table(self, table_id):
        """표 하나의 근거 재료: 쪽, 적용 범위 후보, 셀 목록, 담긴 위치 문맥."""
        from doc_understanding.rag.evidence.cells import cells_for_table
        from doc_understanding.rag.evidence.scope import table_scope
        st = next(t for t in self.doc.table_structure if t.table_id == table_id)
        return dict(table_id=table_id, page=st.page_number, scope=[s["text_raw"] for s in table_scope(st)],
                    cells=cells_for_table(self.doc, table_id), nested_context=self._nested()[0].get(table_id))

    def cells(self, table_id):
        return self.table(table_id)["cells"]

    def nested_context(self, table_id):
        return self._nested()[0].get(table_id)

    def contents(self):
        """글자가 있는 본문 내용 전체(문서 순서). body는 본문 근거로 쓸 수 있는 내용에만 있다."""
        if self._contents is None:
            from doc_understanding.rag.evidence.document import document_label
            doc, owned, label = self.doc, self._nested()[2], document_label(self.doc)
            origin = dict(document_id=doc.document_id, source_name=doc.document_ir.source_name, document_label=label,
                          source_uri=doc.source_uri, source_media_type=doc.document_ir.source.media_type)
            out = {}
            for position, c in enumerate(doc.contents):
                if not c.text_raw or not c.source_references:
                    continue
                first = c.source_references[0].model_dump(mode="json")
                body = None
                if not c.duplicate_of_table_ids and c.content_id not in owned:
                    body = dict(**origin,
                                page=c.source_references[0].page_number, table_id=None, cell_id=None,
                                content_id=c.content_id, value_raw=c.text_raw, value_kind="body",
                                row_path=[], column_path=[], unit_context=[], notes=[],
                                semantic_status="unvalidated_candidate",
                                source_references=[r.model_dump(mode="json") for r in c.source_references],
                                span_refs=[r.span_id for r in c.source_references if r.span_id], source=first)
                out[c.content_id] = dict(content_id=c.content_id, position=position, text_raw=c.text_raw,
                                         source=first, document=origin, body=body)
            self._contents = out
        return self._contents

    def content(self, content_id):
        return self.contents().get(content_id)

    def content_items(self, content_ids):
        items = [self.contents()[i] for i in content_ids if i in self.contents()]
        return sorted(items, key=lambda item: item["position"])

    def restore_hits(self, hits):
        """검색 항목을 지금 문서에서 다시 만들어 대조한다(검색용 문구·범위가 바뀌었으면 근거로 쓰지 않는다)."""
        from doc_understanding.rag.evidence.projections import restore_projection_hits
        return restore_projection_hits(self.doc, hits)

    def chunk(self, hit):
        """칸 안 문장 검색 조각(cell_text). 문서에서 다시 만든 조각을 쓴다."""
        return {c["entry_id"]: c for c in self._nested()[1]}.get(hit.get("entry_id"))


class StoredSource:
    """적재 때 저장한 항목을 ID로 읽는다. load(kind, ids) → {id: payload}."""

    def __init__(self, document, load):
        self._load, self._cache = load, {}
        self.document_id = document["document_id"]

    def _get(self, kind, ids):
        missing = [i for i in dict.fromkeys(ids) if (kind, i) not in self._cache]
        if missing:
            found = self._load(kind, missing)
            for i in missing:
                self._cache[(kind, i)] = found.get(i)
        # 호출자가 근거에 evidence_id 등을 덧붙이므로 매번 새 사본을 준다.
        return {i: copy.deepcopy(self._cache[(kind, i)]) for i in ids if self._cache[(kind, i)] is not None}

    def preload(self, table_ids=(), content_ids=()):
        """여러 항목을 한 번에 읽어 둔다(질문 한 건의 DB 왕복 수를 줄인다)."""
        self._get(TABLE, list(table_ids))
        self._get(CONTENT, list(content_ids))

    def table(self, table_id):
        found = self._get(TABLE, [table_id])
        if table_id not in found:
            raise ValueError("EVIDENCE_STORE_TABLE_NOT_FOUND")
        return found[table_id]

    def cells(self, table_id):
        return self.table(table_id)["cells"]

    def nested_context(self, table_id):
        return self.table(table_id)["nested_context"]

    def content(self, content_id):
        return self._get(CONTENT, [content_id]).get(content_id)

    def content_items(self, content_ids):
        return sorted(self._get(CONTENT, list(content_ids)).values(), key=lambda item: item["position"])

    def restore_hits(self, hits):
        """원문 범위를 가진 검색 조각을 색인에 저장된 기준 항목과 대조한다(문구·범위가 바뀐 조각은 근거로 쓰지 않는다).
        기준 항목은 저장본과 같은 트랜잭션에서 같은 문서로 만든 것이다."""
        from doc_understanding.rag.evidence.projections import check_projection_hits, projected_hits
        ids = [h.get("entry_id") for h in projected_hits(hits) if h.get("entry_id")]
        if not projected_hits(hits):
            return hits
        return check_projection_hits(hits, self._get(ENTRY, ids))

    def chunk(self, hit):
        return hit if hit.get("kind") == "cell_text" else None


def as_source(value):
    """문서를 받으면 DocumentSource로 감싼다. 이미 출처면 그대로."""
    return value if isinstance(value, (DocumentSource, StoredSource)) else DocumentSource(value)


def evidence_items(doc):
    """색인과 함께 저장할 항목들: (kind, item_id, payload). payload는 JSON으로 왕복해도 같은 값이어야 한다."""
    source = DocumentSource(doc)
    items = [(TABLE, st.table_id, source.table(st.table_id)) for st in doc.table_structure
             if any(p.table_id == st.table_id for p in doc.physical_tables)
             and any(c.table_id == st.table_id for c in doc.contents)]
    items += [(CONTENT, cid, item) for cid, item in source.contents().items()]
    for kind, item_id, payload in items:
        if json.loads(json.dumps(payload, ensure_ascii=False)) != payload:
            raise ValueError(f"EVIDENCE_ITEM_NOT_JSON_STABLE:{kind}:{item_id}")
    return items
