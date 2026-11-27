# 역할: 본문·표 요약·행·중첩 본문을 원본 참조가 있는 검색 항목으로 만든다.
from doc_understanding.rag.evidence.cells import cells_for_table, display_path
from doc_understanding.rag.evidence.containers import containment
from doc_understanding.rag.evidence.document import document_label
from doc_understanding.rag.evidence.scope import table_scope
from doc_understanding.rag.indexing.text import body_entries, long_cell_entries


def make_entries(doc):
    from doc_understanding.rag.indexing.nested import nested_projection
    nested, chunks, owned = nested_projection(doc)
    holders = containment(doc)["containers"]
    contents = {c.content_id: c for c in doc.contents}
    sections = {s.section_id: s.title_raw for s in doc.sections}
    entries = []
    # 모든 항목 맨 앞에 문서 이름표(파일 이름 + 첫 쪽 제목)를 둔다: 질문이 기관·상품 이름으로 문서를 가리킬 때
    # 같은 양식의 다른 문서와 구분된다.
    label = document_label(doc)
    for c in doc.contents:
        if c.duplicate_of_table_ids:
            continue
        context = [contents[cid].text_raw or '' for cid in c.context_content_ids]
        prefix = [label, sections.get(c.section_id, '')] + context
        if c.kind == 'table':
            table = next(t for t in doc.table_structure if t.table_id == c.table_id)
            rows = cells_for_table(doc, c.table_id)
            pt=next(t for t in doc.physical_tables if t.table_id==c.table_id)
            header_count=max((p.header_rows for p in table.panels),default=1)
            header_text=[cell.original_text or '' for cell in pt.cells
                         if cell.row_index is not None and cell.row_index<header_count]
            # 구조 검토 후보의 적용 범위(표가 속한 소제목)를 표·행 항목 맨 앞에 둔다. 없으면 기존과 같다.
            header_text = ['적용 범위: ' + s['text_raw'] for s in table_scope(table)] + header_text
            primary = header_text + [n.label_raw or '' for n in table.row_tree + table.column_tree]
            summary = prefix + primary
            entries.append(dict(entry_id=c.content_id, content_id=c.content_id, kind='table',
                                table_id=c.table_id, page=table.page_number, cell_ids=[],
                                text='\n'.join(summary),primary_text='\n'.join(primary),
                                context_content_ids=list(c.context_content_ids)))
            by_row = {}
            for row in rows:
                key = tuple(row['row_node_ids'])
                by_row.setdefault(key, []).append(row)
            for i, row_values in enumerate(by_row.values()):
                # 내용을 담는 칸의 원문은 안쪽 표·칸 안 문장과 중복이라 값으로 넣지 않는다. 대신 '칸 안 내용' 표시와
                # 안쪽 표 ID(child_table_ids)를 남겨, 이 행이 검색되면 근거 복원이 안쪽 표로 내려가게 한다.
                values = [v for v in row_values if not v.get('container_duplicate')]
                held = [v for v in row_values if v.get('container_duplicate')]
                children = [it['table'] for v in held for it in holders[v['cell_id']]['items'] if it['kind'] == 'table']
                primary = header_text + [display_path(row_values[0]['row_path'])]
                primary += [display_path(v['column_path']) + ': ' + (v['value_raw'] if v['value_raw'] is not None else '[unavailable]')
                         for v in values]
                primary += [display_path(v['column_path']) + ': [칸 안 내용: 표 ' + str(len(children)) + '개와 문장]' for v in held]
                text = prefix + primary
                entry = dict(entry_id=c.content_id + f'/row{i}', content_id=c.content_id,
                             kind='table_row', table_id=c.table_id, page=table.page_number,
                             cell_ids=[v['cell_id'] for v in values], text='\n'.join(text),primary_text='\n'.join(primary),
                             context_content_ids=list(c.context_content_ids))
                if children:
                    entry['child_table_ids'] = children
                entries.append(entry)
                entries.extend(long_cell_entries(c, values, prefix, header_text))
    entries.extend(body_entries(doc, owned))
    for entry in entries:
        scope=nested.get(entry.get('table_id'))
        if scope:
            # 칸 안에 담긴 표: 담은 칸 라벨 경로와 바깥 칸 문장, 칸 안 문장을 함께 검색되게 한다(적용 판단은 하지 않음).
            if scope.get('path'):
                entry['text']+='\n담긴 위치: '+' › '.join(p['label'] for p in scope['path'])
            entry['text']+='\n'+'\n'.join(p['text_raw'] for p in scope.get('outer_prose', [])+scope['prose'])
            if scope.get('parent_physical_cell_id'):
                entry['parent_physical_cell_id'] = scope['parent_physical_cell_id']
            entry['parent_cell_id']=scope['parent_cell_id']
    return entries+chunks
