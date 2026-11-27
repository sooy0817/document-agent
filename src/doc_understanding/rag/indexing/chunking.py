# 역할: 원문을 바꾸지 않고 검색 조각의 문자 범위와 보수적인 문단 묶음을 만든다.
import re

TEXT_CHUNK_CHARS = 600
PARAGRAPH_GROUP_CHARS = 2400
PROJECTION_VERSION = 'body-cell-text/1'


def text_slices(text, limit=TEXT_CHUNK_CHARS):
    """원문 전체를 중복·누락 없는 반열린 문자 범위로 나눈다."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError('INVALID_CHUNK_LIMIT')
    result = []
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            candidates = [m.end() for m in re.finditer(r'\n|[.!?。]\s+|\s+', text[start:end])]
            if candidates and candidates[-1] >= limit // 2:
                end = start + candidates[-1]
        result.append((start, end))
        start = end
    return result


def _block_key(content):
    refs = content.source_references
    keys = {(r.document_id, r.page_number, r.block_id) for r in refs}
    return next(iter(keys)) if len(keys) == 1 and refs[0].block_id else None


def _adjacent(previous, current):
    # 같은 블록이라도 옆 단이나 겹친 줄이면 합치지 않는다. 임계값은 배치 후보 정책이다.
    if (previous.kind != 'paragraph' or current.kind != 'paragraph'
            or previous.section_id != current.section_id
            or current.reading_order != previous.reading_order + 1
            or _block_key(previous) is None or _block_key(previous) != _block_key(current)):
        return False
    a, b = previous.source_references, current.source_references
    ax0, ax1 = min(r.bbox.x0 for r in a), max(r.bbox.x1 for r in a)
    bx0, bx1 = min(r.bbox.x0 for r in b), max(r.bbox.x1 for r in b)
    ay0, ay1 = min(r.bbox.y0 for r in a), max(r.bbox.y1 for r in a)
    by0, by1 = min(r.bbox.y0 for r in b), max(r.bbox.y1 for r in b)
    width = min(ax1 - ax0, bx1 - bx0)
    height = min(ay1 - ay0, by1 - by0)
    return (width > 0 and height > 0 and 0 <= by0 - ay1 <= height * 1.5
            and min(ax1, bx1) - max(ax0, bx0) >= width * .5)


def paragraph_groups(doc, excluded_ids):
    """표·주석·섹션 제목·페이지·블록 경계를 넘지 않는 검색용 후보 묶음이다."""
    group = []
    size = 0
    for content in sorted(doc.contents, key=lambda c: c.reading_order):
        allowed = (not content.table_id and not content.duplicate_of_table_ids
                   and content.content_id not in excluded_ids
                   and bool((content.text_raw or '').strip()))
        heading = any(content.source_references == s.source_references for s in doc.sections)
        join = (allowed and group and not heading and doc.reading_order_status != 'unresolved'
                and _adjacent(group[-1], content)
                and size + 1 + len(content.text_raw) <= PARAGRAPH_GROUP_CHARS)
        if group and not join:
            yield group
            group, size = [], 0
        if allowed:
            group.append(content)
            size += len(content.text_raw) + (1 if len(group) > 1 else 0)
            if heading or content.kind != 'paragraph':
                yield group
                group, size = [], 0
    if group:
        yield group
