# 역할: 본문·셀 검색 조각을 현재 원문에서 재생성한 표현과 대조한다.
from doc_understanding.rag.indexing.chunking import PROJECTION_VERSION


def projected_hits(hits):
    """원문 범위를 가진 검색 조각(본문 묶음·긴 셀 조각). 근거로 쓰기 전에 기준 항목과 대조한다."""
    return [h for h in hits if ('source_segments' in h or 'content_ids' in h
        or h.get('projection_version') == PROJECTION_VERSION or h.get('kind') == 'table_cell_chunk')]


def restore_projection_hits(doc, hits):
    """검색용 문구나 범위가 바뀌었으면 원문 근거로 채택하지 않는다."""
    from doc_understanding.rag.indexing.entries import make_entries

    if not projected_hits(hits):
        return hits
    return check_projection_hits(hits, {e['entry_id']: e for e in make_entries(doc)})


def check_projection_hits(hits, originals):
    """originals(항목 ID → 기준 항목: 문서에서 다시 만든 것 또는 색인에 저장된 것)와 다르면 거부한다."""
    projected = projected_hits(hits)
    for hit in projected:
        expected = originals.get(hit.get('entry_id'))
        if (hit.get('projection_version') != PROJECTION_VERSION or expected is None
                or any(hit.get(key) != value for key, value in expected.items())):
            raise ValueError('SEARCH_PROJECTION_SOURCE_MISMATCH')
    return [originals[h['entry_id']] if h in projected else h for h in hits]
