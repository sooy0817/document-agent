# 역할: 검색된 행·표·본문에서 근거를 복원한다. 기존 셀 한도와 미해결 매핑 제외 정책을 유지한다.
from doc_understanding.rag.evidence.cells import display_path
from doc_understanding.rag.evidence.source import as_source
from doc_understanding.rag.retrieval.lexical import lexical_score


def evidence_pack(doc, hits, question, max_cells=35):
    """Bound context to retrieved rows; query scores order evidence, never create values.

    doc는 문서(CanonicalDocument)나 근거 출처(rag.evidence.source)다. 질문 경로는 적재 때 저장한 출처를 넘겨
    문서 전체를 읽지 않는다. 두 경우의 결과는 같다.
    """
    from doc_understanding.rag.evidence.nested import narrative_evidence
    source = as_source(doc)
    hits = source.restore_hits(hits)
    chunk_index = {h['entry_id']: c for h in hits if h.get('entry_id') for c in [source.chunk(h)] if c}
    expanded=list(hits)
    for hit in hits:
        if hit.get('kind')=='cell_text' and hit.get('entry_id') in chunk_index:
            for tid in chunk_index[hit['entry_id']]['child_table_ids']:
                expanded.append(dict(kind='table',table_id=tid))
        # 내용을 담는 칸이 있는 행: 칸 안에 담긴 표로 내려가 근거를 복원한다(칸 원문은 중복이라 값이 아니다).
        for tid in hit.get('child_table_ids') or []:
            expanded.append(dict(kind='table',table_id=tid))
    # Include adjacent body and notes even for a table-only hit.
    context_ids = {cid for h in hits for cid in h.get('context_content_ids', [])}
    context_ids |= {h['content_id'] for h in hits if not h.get('table_id')}
    # 검색 조각만 답변에 넘기지 않고 같은 문단 후보의 원문 Content들을 각각 복원한다.
    context_ids |= {cid for h in hits if not h.get('table_id') for cid in h.get('content_ids', [])}
    if hasattr(source, 'preload'):
        # 필요한 표·본문을 한 번에 읽는다.
        source.preload(
            list(dict.fromkeys([h['table_id'] for h in expanded if h.get('table_id')]
                               + [t for c in chunk_index.values() for t in c['child_table_ids']])),
            sorted(context_ids | {c['content_id'] for c in chunk_index.values()}))
    selected, seen = [], set()
    for hit in expanded:
        if hit.get('table_id'):
            nested_context = source.nested_context(hit['table_id'])
            for v in source.cells(hit['table_id']):
                if not v['lookup_eligible']:
                    continue
                allowed_cells = (hit.get('row_cell_ids', []) if hit['kind'] == 'table_cell_chunk'
                                 else hit.get('cell_ids', []))
                if hit['kind'] in ('table_row', 'table_cell_chunk') and v['cell_id'] not in allowed_cells:
                    continue
                if v['cell_id'] not in seen:
                    if nested_context:v['nested_context']=nested_context
                    selected.append(v)
                    seen.add(v['cell_id'])
    direct_ids = {cid for h in hits if h.get('kind') == 'table_cell_chunk' for cid in h['cell_ids']}
    def score(v):
        # 검색된 셀 전체를 우선 복원하고 같은 행의 다른 값은 기존 근거 한도 안에서 선택한다.
        return (v['cell_id'] in direct_ids,
                lexical_score(display_path(v['row_path'] + v['column_path']) + ' ' + (v['value_raw'] or ''), question))
    selected.sort(key=score, reverse=True)
    selected = selected[:max_cells]
    for i, v in enumerate(selected, 1):
        v['evidence_id'] = f'E{i}'
    for hit in hits:
        chunk=chunk_index.get(hit.get('entry_id'))
        if chunk and chunk['entry_id'] not in seen:
            context=next((c for c in (source.nested_context(t) for t in chunk['child_table_ids']) if c),None)
            if context:
                e=narrative_evidence(source,chunk,context)
                e['evidence_id']=f'E{len(selected)+1}'
                selected.append(e);seen.add(chunk['entry_id'])
    for item in source.content_items(context_ids):
        # 본문 근거로 쓸 수 있는 내용만(표와 중복이거나 칸 안 문장으로 따로 다뤄지는 내용은 body가 없다).
        if item['body']:
            selected.append(dict(evidence_id=f'E{len(selected)+1}', **item['body']))
    return selected
