# 역할: 포함·읽기 순서 근거가 충분한 부모 셀의 본문을 검색 조각과 문맥으로 투영한다.
import hashlib
from doc_understanding.parser.assembly.nested_content import build_nested_content
from doc_understanding.rag.evidence.containers import MAX_CONTEXT_CHARS, container_context, containment
from doc_understanding.rag.indexing.chunking import text_slices as text_slices
from doc_understanding.rag.evidence.document import document_label

VERSION = 'nested-evidence/3'
_PROJECTIONS = {}


def nested_projection(doc):
    """문서마다 한 번 계산한다(근거 복원·검색 항목·질의 도구가 같은 문서를 여러 번 본다)."""
    key = (id(doc), id(build_nested_content))
    if key in _PROJECTIONS and _PROJECTIONS[key][0] is doc:
        return _PROJECTIONS[key][1]
    value = _projection(doc)
    if len(_PROJECTIONS) >= 8:
        _PROJECTIONS.pop(next(iter(_PROJECTIONS)))
    _PROJECTIONS[key] = (doc, value)
    return value


def _container_path(doc, table_id, info):
    """담긴 위치 라벨 경로와 바깥 칸(바로 담은 칸 제외)의 앞뒤 문장. 셀 안 = 하나의 내용, 안쪽 표 = 그 안의 문맥."""
    levels = container_context(doc, table_id, info)
    contents = {c.content_id: c for c in doc.contents}
    # label은 표시용(공백 정리·' › ' 연결)이다. 인용 후보는 labels(라벨 칸 원문·출처)를 쓴다.
    path = [dict(label=' › '.join(lv['label']), cell_ids=lv['label_cell_ids'], container_cell_id=lv['cell_id'],
                 labels=lv.get('labels', []))
            for lv in levels if lv['label']]
    outer = [dict(content_id=a['content_id'], text_raw=contents[a['content_id']].text_raw,
                  source_references=[s.model_dump(mode='json') for s in contents[a['content_id']].source_references])
             for lv in levels[:-1] for a in lv['around'] if a['content_id'] in contents]
    return path, outer


def _projection(doc):
    info=containment(doc)
    if doc.document_ir and doc.document_ir.source.media_type == 'text/html':
        contexts = {}
        for table in doc.physical_tables:
            owner = info['parent_of'].get(table.table_id)
            if owner is None:
                continue
            path, outer = _container_path(doc, table.table_id, info)
            children = [item['table'] for item in info['containers'][owner]['items']]
            contexts[table.table_id] = dict(
                parent_cell_id=owner, parent_physical_cell_id=owner, parent_table_id=info['cell_table'][owner],
                page=table.page_number, child_table_ids=children, containment_status='linked',
                coverage_status='not_assessed', reading_order_status='unresolved', semantic_status='not_validated',
                items=[dict(kind='table', ref_id=child) for child in children], prose=[], outer_prose=outer,
                path=path, linkage_algorithm='html-dom/1', projection_version=VERSION)
        return contexts, [], set()
    links=build_nested_content(doc)
    contents={c.content_id:c for c in doc.contents}
    contexts={};chunks=[];owned=set()
    for link in links.cells:
        owned.update(i.ref_id for i in link.items if i.kind=='content')
        # Do not turn ambiguous containment/order into an answerable scope.
        if (link.containment_status!='linked' or link.coverage_status!='complete'
                or link.reading_order_status!='total' or not link.child_table_ids):continue
        child_spans={s for i in link.items if i.kind!='content' for s in i.span_ids}
        prose=[]
        for item in link.items:
            if item.kind!='content' or child_spans.intersection(item.span_ids):continue
            c=contents[item.ref_id]
            if not (c.text_raw or '').strip():continue
            prose.append(dict(content_id=c.content_id,text_raw=c.text_raw,span_refs=list(item.span_ids),
                              source_references=[s.model_dump(mode='json') for s in c.source_references]))
        # Avoid silent condition truncation; oversized packages remain searchable as tables only.
        if sum(len(p['text_raw']) for p in prose)>MAX_CONTEXT_CHARS:continue
        context=dict(parent_cell_id=link.parent_cell_id,parent_table_id=link.parent_native_table_id,
                     page=link.page_number,child_table_ids=list(link.child_table_ids),
                     containment_status=link.containment_status,coverage_status=link.coverage_status,
                     reading_order_status=link.reading_order_status,semantic_status='not_validated',
                     items=[dict(kind=i.kind,ref_id=i.ref_id) for i in link.items],prose=prose,
                     linkage_algorithm=links.linkage_algorithm,projection_version=VERSION)
        for table_id in link.child_table_ids:
            # Competing owners are not resolved by arbitrary overwrite.
            path,outer=_container_path(doc,table_id,info)
            contexts[table_id]=dict(context,path=path,outer_prose=outer,
                                    parent_physical_cell_id=info["parent_of"].get(table_id)) if table_id not in contexts else None
        for p in prose:
            for start,end in text_slices(p['text_raw']):
                raw=p['text_raw'][start:end]
                if not raw.strip():continue
                identity=f"{link.parent_cell_id}:{p['content_id']}:{start}:{end}:{VERSION}"
                chunks.append(dict(entry_id=p['content_id']+'/chunk/'+hashlib.sha256(identity.encode()).hexdigest()[:20],
                    content_id=p['content_id'],kind='cell_text',table_id=None,page=link.page_number,cell_ids=[],
                    primary_text=raw,text=document_label(doc)+'\n'+raw,context_content_ids=[],
                    parent_cell_id=link.parent_cell_id,parent_table_id=link.parent_native_table_id,
                    child_table_ids=list(link.child_table_ids),char_start=start,char_end=end,
                    span_refs=p['span_refs'],projection_version=VERSION))
    return contexts,chunks,owned
