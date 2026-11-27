# 역할: 저장된 물리 복원 후보를 외부 셀·경로 정답과 비교한다. 파서 실행이나 입력 변경은 하지 않는다.
import unicodedata
from collections import Counter


def box(b):
    return [b[k] for k in ('x0','y0','x1','y1')] if isinstance(b,dict) else b


def area(b):
    b=box(b)
    return max(0,b[2]-b[0])*max(0,b[3]-b[1])


def intersection(a,b):
    a,b=box(a),box(b)
    return max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))


def norm(s):
    return ''.join(unicodedata.normalize('NFKC',s or '').split()).casefold()


def matching_tables(tables,target):
    return [t for t in tables if intersection(t['bounding_box'],target)/max(area(target),1)>=.7
            and intersection(t['bounding_box'],target)/max(area(t['bounding_box']),1)>=.5]


def evaluate(case,physical,structured):
    tables=matching_tables(physical['tables'],case['bbox'])
    table=max(tables,key=lambda t:intersection(t['bounding_box'],case['bbox']),default=None)
    st=next((s for s in structured if table and s['table_id']==table['table_id']),None)
    cells=table['cells'] if table else []
    exact=[]
    probes=[]
    for ref in case.get('cells',[]):
        matches=[c for c in cells if norm(c['original_text'])==norm(ref['text'])
                 and max(abs(x-y) for x,y in zip(box(c['source_bbox']),ref['bbox']))<=3]
        exact.append(dict(id=ref['id'],matched=len(matches)==1,
            row=bool(len(matches)==1 and matches[0]['row_index']==ref['row']),
            column=bool(len(matches)==1 and matches[0]['column_index']==ref['column']),
            span=bool(len(matches)==1 and matches[0]['rowspan']==ref['rowspan'] and matches[0]['colspan']==ref['colspan'])))
    for ref in case.get('probes',[]):
        b=ref['cell_bbox']
        x,y=(b[0]+b[2])/2,(b[1]+b[3])/2
        contains=[c for c in cells if c['source_bbox']['x0']<=x<c['source_bbox']['x1']
                  and c['source_bbox']['y0']<=y<c['source_bbox']['y1']]
        good=len(contains)==1 and norm(contains[0]['original_text'])==norm(ref['value_raw'])
        m=next((m for m in st['cell_mappings'] if good and m['cell_id']==contains[0]['cell_id']),None) if st else None
        idx={n['node_id']:n for n in st['row_tree']+st['column_tree']} if st else {}
        paths={axis:[idx[i]['label_raw'] for i in m[axis]] if m else [] for axis in ('row_path','column_path')}
        row=bool(m and [norm(x) for x in paths['row_path']]==[norm(x) for x in ref['row_path']])
        col=bool(m and [norm(x) for x in paths['column_path']]==[norm(x) for x in ref['column_path']])
        probes.append(dict(id=ref['id'],cell_exact=good,row_path_exact=row,column_path_exact=col,
            wrong_value_at_source=bool(contains and not good),
            wrong_row_parent=bool(paths['row_path'] and not row),wrong_column_parent=bool(paths['column_path'] and not col),
            mapping_state=m['evidence_state'] if m else 'missing',paths=paths,
            source=ref,actual_cells=[dict(id=c['cell_id'],text=c['original_text'],bbox=c['source_bbox']) for c in contains]))
    known=case.get('cells',[])
    false_cuts=0
    if table and known:
        for edge in table['boundaries']:
            if edge['status']=='absent':
                continue
            for ref in known:
                x0,y0,x1,y1=ref['bbox']
                if (edge['axis']=='vertical' and x0+3<edge['position']<x1-3
                    and min(y1-3,edge['end'])>max(y0+3,edge['start'])) or (
                    edge['axis']=='horizontal' and y0+3<edge['position']<y1-3
                    and min(x1-3,edge['end'])>max(x0+3,edge['start'])):
                    false_cuts+=1
                    break
    return dict(id=case['id'],source_id=case['source_id'],page=case['page'],split=case['split'],
        detected=bool(table),row_count=len(table['rows']) if table else 0,column_count=len(table['columns']) if table else 0,
        row_count_exact=bool(table and len(table['rows'])==case['rows']) if case.get('rows') else None,
        column_count_exact=bool(table and len(table['columns'])==case['columns']) if case.get('columns') else None,
        cell_count=len(cells),full_cell_reference_count=len(known),full_cell_checks=exact,probes=probes,
        false_boundary_segments_in_full_references=false_cuts,
        extra_or_wrong_cells=len(cells)-sum(x['matched'] for x in exact) if known else None,
        issues=Counter(i['code'] for i in table['validation_issues']) if table else {},
        ambiguities=Counter(a['code'] for a in st['ambiguities']) if st else {},
        errors=[i['code'] for i in table['validation_issues'] if i['severity']=='error'] if table else [])
