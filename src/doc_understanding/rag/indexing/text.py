# 역할: 본문 문단과 긴 셀을 원문 ID·문자 범위를 가진 검색 항목으로 표현한다.
import hashlib
import json

from doc_understanding.rag.indexing.chunking import (
    PROJECTION_VERSION, TEXT_CHUNK_CHARS, paragraph_groups, text_slices,
)
from doc_understanding.rag.evidence.cells import display_path
from doc_understanding.rag.evidence.document import document_label


def _identity(parent, members, start, end):
    payload = json.dumps([PROJECTION_VERSION, members, start, end], ensure_ascii=False)
    return parent + '/text/' + hashlib.sha256(payload.encode('utf-8')).hexdigest()[:20]


def body_entries(doc, excluded_ids):
    sections = {s.section_id: s.title_raw for s in doc.sections}
    entries = []
    label = document_label(doc)
    for group in paragraph_groups(doc, excluded_ids):
        raw = '\n'.join(c.text_raw for c in group)
        ids = [c.content_id for c in group]
        first = group[0]
        for start, end in text_slices(raw):
            segments = []
            offset = 0
            for c in group:
                left, right = max(start, offset), min(end, offset + len(c.text_raw))
                if left < right:
                    segments.append(dict(content_id=c.content_id, char_start=left - offset,
                        char_end=right - offset, chunk_start=left - start, chunk_end=right - start,
                        source_references=[r.model_dump(mode='json') for r in c.source_references]))
                offset += len(c.text_raw) + 1
            # 결합 줄바꿈은 검색 표현일 뿐 원문 인용이 아니다. 각 조각의 원본 범위를 따로 둔다.
            part = raw[start:end]
            entry_id = (first.content_id if len(group) == 1 and end == len(raw) and start == 0
                        else _identity(first.content_id, ids, start, end))
            entries.append(dict(entry_id=entry_id, content_id=first.content_id, content_ids=ids,
                kind=first.kind, table_id=None, page=first.source_references[0].page_number,
                cell_ids=[], context_content_ids=[], primary_text=part,
                text='\n'.join([label, sections.get(first.section_id, ''), part]),
                char_start=start, char_end=end, join_separator='\n', source_segments=segments,
                projection_version=PROJECTION_VERSION))
    return entries


def long_cell_entries(content, values, prefix, header_text):
    entries = []
    row_ids = [v['cell_id'] for v in values]
    for value in values:
        raw = value['value_raw']
        if raw is None or len(raw) <= TEXT_CHUNK_CHARS:
            continue
        for start, end in text_slices(raw):
            part = raw[start:end]
            entries.append(dict(
                entry_id=_identity(value['cell_id'], row_ids, start, end),
                content_id=content.content_id, kind='table_cell_chunk', table_id=value['table_id'],
                page=value['page'], cell_ids=[value['cell_id']], row_cell_ids=row_ids,
                row_path=value['row_path'], column_path=value['column_path'],
                lookup_eligible=value['lookup_eligible'], primary_text=part,
                text='\n'.join(prefix + header_text + [display_path(value['row_path']),
                    display_path(value['column_path']), part]),
                context_content_ids=list(content.context_content_ids),
                char_start=start, char_end=end, span_refs=value['span_refs'],
                source_segments=[dict(cell_id=value['cell_id'], char_start=start, char_end=end,
                    chunk_start=0, chunk_end=end - start, source_references=[value['source']])],
                projection_version=PROJECTION_VERSION))
    return entries
