"""Evaluation only: source annotations never enter a retriever or a prompt."""
import re
import unicodedata


def label(text):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', text or '')).strip().casefold()


def overlap(a, b):
    intersection = max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    return intersection/max(1,(a[2]-a[0])*(a[3]-a[1]))


def target_tables(doc, reference):
    return [t.table_id for t in doc.physical_tables if t.page_number==reference['page']
            and overlap(reference['table_bbox'],t.bounding_box.xyxy)>=.7
            and overlap(t.bounding_box.xyxy,reference['table_bbox'])>=.5]


def path_equal(actual, expected):
    return [label(x) for x in actual] == [label(x) for x in expected]


def score(case, state, docs, source_doc, document_hits):
    ref = case['reference']
    tables = target_tables(source_doc,ref) if ref.get('table_bbox') else []
    candidates = state.get('candidates',[])
    e = state.get('evidence',[])
    result = state.get('result',{})
    answered = bool(e) and not result.get('selection',{}).get('abstained',True) and not state.get('errors')
    first = candidates[0] if candidates else None
    ids = [c['payload']['table_id'] for c in candidates]
    table_ok = bool(first and first['document_id']==source_doc.document_id and first['table_id'] in tables)
    selected = e[0] if answered and len(e)==1 else None
    row = path_equal(selected['row_path'],ref['row_path']) if selected and 'row_path' in ref else False
    col = path_equal(selected['column_path'],ref['column_path']) if selected and 'column_path' in ref else False
    value = bool(selected and label(selected['value_raw'])==label(ref.get('value_raw')))
    origin = bool(selected and selected['document_id']==source_doc.document_id and selected['page']==ref['page']
                  and selected['table_id'] in tables and overlap(ref['cell_bbox'],list(selected['source']['bbox'].values()))>=.5)
    expected_answer = not ref.get('expected_abstained',False)
    section = None
    if ref.get('section_title'):
        section = False
        if first:
            doc=docs[first['run_id']]
            content=next(c for c in doc.contents if c.table_id==first['table_id'])
            s=next((s for s in doc.sections if s.section_id==content.section_id),None)
            section=bool(s and label(ref['section_title']) in label(s.title_raw))
    fields = dict(document_top1=bool(document_hits and docs[document_hits[0]['run_id']].document_id==source_doc.document_id),
        table_recall_at_5=bool(set(ids[:5]).intersection(tables)) if ref.get('table_bbox') else None,
        table_top1=table_ok if ref.get('table_bbox') else None,
        section_top1=section, row_path_exact=row if expected_answer else None,
        column_path_exact=col if expected_answer else None,
        cell_exact=bool(value and origin) if expected_answer else None,
        evidence_exact=bool(value and origin and row and col) if answered else None,
        source_exact=origin if answered else None,
        period_exact=all(label(y) in label(' '.join(selected['column_path'])) for y in ref['periods'])
            if selected and ref.get('periods') else None,
        unit_exact=any(label(ref['unit_text'])==label(u['text_raw']) for u in selected['unit_context'])
            if selected and ref.get('unit_text') else None,
        note_exact=any(label(ref['note_text'])==label(n['text_raw']) for n in selected['notes'])
            if selected and ref.get('note_text') else None,
        abstention_correct=not answered if ref.get('expected_abstained') else None,
        answered=answered, parser_found_target=bool(tables), errors=bool(state.get('errors')))
    return fields


def aggregate(records):
    keys = ('document_top1','section_top1','table_recall_at_5','table_top1','row_path_exact',
            'column_path_exact','cell_exact','evidence_exact','source_exact','period_exact','unit_exact','note_exact',
            'abstention_correct')
    metrics={}
    for key in keys:
        values=[r['metrics'][key] for r in records if r['metrics'][key] is not None]
        metrics[key]=dict(numerator=sum(values),denominator=len(values),value=sum(values)/len(values) if values else None)
    return dict(cases=len(records),metrics=metrics,answered=sum(r['metrics']['answered'] for r in records),
        errors=sum(r['metrics']['errors'] for r in records),
        mean_seconds=sum(r['elapsed_seconds'] for r in records)/len(records) if records else None)
