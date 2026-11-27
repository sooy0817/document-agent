# 역할: 중첩 본문 조각을 원문 위치로 다시 읽고 검색 투영과의 일치를 검사한다.
def narrative_evidence(doc, chunk, context):
    """칸 안 문장 조각의 근거. doc는 문서나 근거 출처(rag.evidence.source)다."""
    from doc_understanding.rag.evidence.source import as_source
    c=as_source(doc).content(chunk['content_id'])
    if c is None:raise ValueError('NARRATIVE_SOURCE_MISMATCH')
    raw=c['text_raw'][chunk['char_start']:chunk['char_end']]
    if raw!=chunk['primary_text']:raise ValueError('NARRATIVE_SOURCE_MISMATCH')
    return dict(**c['document'],page=chunk['page'],
        table_id=None,cell_id=None,content_id=c['content_id'],chunk_id=chunk['entry_id'],
        parent_cell_id=chunk['parent_cell_id'],char_start=chunk['char_start'],char_end=chunk['char_end'],
        value_raw=raw,value_kind='cell_text',row_path=[],column_path=[],unit_context=[],notes=[],
        lookup_eligible=True,semantic_status='unvalidated_candidate',nested_context=context,
        span_refs=chunk['span_refs'],source=c['source'])
