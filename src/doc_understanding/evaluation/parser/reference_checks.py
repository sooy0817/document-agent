# 역할: 외부 참조의 중첩 연결·읽기 순서·원문 인용을 결과와 대조한다. 참조를 파서 입력에 넣지 않는다.
"""Read-only, source-referenced acceptance checks; never input to extraction."""
from collections import Counter
from doc_understanding.evaluation.parser.snapshots import signature


def evaluate(document, nested, reference):
    if reference.get('schema_version') == 'human-reference/1.0':
        return evaluate_feedback(document, nested, reference)
    parent = reference['parent_cell_id']
    found = [c for c in nested['cells'] if c['parent_cell_id'] == parent]
    link = found[0] if len(found) == 1 else {}
    items = link.get('items', [])
    children = reference['child_table_ids']
    def position(anchor):
        indexes = [i for i,x in enumerate(items) if
                   (anchor.get('table_id') == x['ref_id'] if anchor.get('table_id') else
                    anchor.get('span_id') in x['span_ids'])]
        return indexes[0] if len(indexes)==1 else None
    positions = [position(a) for a in reference['reading_anchors']]
    coverage = Counter(s for i in items for s in i['span_ids'])
    checks = [
        ('source', '평가 원본 해시 일치', document['document_id'] == reference['document_id']),
        ('parent', '두 내부 표가 지정한 바깥 설명 셀에 연결',
         len(found)==1 and link.get('containment_status')=='linked' and
         set(link.get('child_table_ids',[]))==set(children)),
        ('once', '각 내부 표를 한 번만 참조', all(sum(i['ref_id']==c for i in items)==1 for c in children)),
        ('coverage', '설명·표·주석 원문 span 누락과 중복 없음',
         bool(link) and link.get('coverage_status')=='complete' and
         all(coverage[s]==1 for s in reference['nonblank_parent_span_ids']) and
         not link.get('outside_parent_span_ids')),
        ('order', '설명 → 위 표 → 주1 설명 → 아래 표 → 정의·주2 순서',
         link.get('reading_order_status')=='total' and all(p is not None for p in positions) and
         all(a<b for a,b in zip(positions,positions[1:]) if a is not None and b is not None)),
        ('no_native_duplicate', '머리말 중복 후보를 별도 표로 남기지 않음',
         bool(items) and not any(i['kind']=='native_table' for i in items)),
        ('wrong_parent', '왼쪽 항목 셀·페이지 테두리에 내부 표를 오연결하지 않음',
         not any(set(c.get('child_table_ids',[])) & set(children) for c in nested['cells'] if c['parent_cell_id']!=parent)),
    ]
    return dict(reference_status=reference['status'], checks=[dict(id=k,label=l,passed=bool(v)) for k,l,v in checks],
                passed=all(v for _,_,v in checks), human_approved=False)


def evaluate_feedback(document, nested, reference):
    """Only explicit, source-grounded containment claims are executable criteria for now.

    Unsupported or uncertain claims block promotion; they are never dropped from the denominator.
    Role/path corrections need a separate acceptance adapter and remain proposals.
    """
    records={r['id']:r for r in reference.get('source_records',[])}
    cells={c['cell_id']:c for t in document['physical_tables'] for c in t['cells']}
    tables={t['table_id']:t for t in document['physical_tables']}
    spans=signature(document)['spans']
    checks=[dict(id='source',label='정답 참조와 원본 문서 일치',
                 passed=document['document_id']==reference['document_id'])]
    claims=reference.get('claims',[])
    checks.append(dict(id='claims',label='확인할 정답 연결이 있음',passed=bool(claims)))
    def norm(text):return ''.join((text or '').split())
    for n,claim in enumerate(claims):
        source,target=claim.get('source_id'),claim.get('target_id')
        supported=claim['kind']=='nested_table' and claim['certainty']=='proposed'
        evidence=claim.get('evidence',[])
        grounded=bool(evidence)
        for e in evidence:
            sid=e['source_id'];record=records.get(sid,{})
            actual=cells.get(sid,{}).get('original_text',spans.get(sid,''))
            quote=norm(e.get('quote'))
            grounded=grounded and bool(quote) and quote in norm(record.get('text')) and quote in norm(actual)
        identified=source in cells and target in tables and source in records and target in records
        matches=[c for c in nested['cells'] if c['parent_cell_id']==source]
        linked=any(c['containment_status']=='linked' and target in c['child_table_ids'] for c in matches)
        correct=(not linked) if claim.get('relation')=='no_link' else linked
        if claim.get('relation')=='link':
            correct=correct and len(matches)==1
            if matches:
                c=matches[0]
                correct=correct and c.get('coverage_status')=='complete' and c.get('reading_order_status')=='total'
                correct=correct and sum(i['ref_id']==target for i in c.get('items',[]))==1
                correct=correct and not any(target in x['child_table_ids'] for x in nested['cells'] if x['parent_cell_id']!=source)
        checks.extend([
            dict(id=f'{n}-supported',label=f'판단 {n+1}: 중첩 연결의 확정 참조 (다른 역할·불확실 판단은 아직 자동 반영 불가)',passed=supported),
            dict(id=f'{n}-evidence',label=f'판단 {n+1}: 지정한 셀·표와 인용 원문 보존',passed=bool(identified and grounded)),
            dict(id=f'{n}-relation',label=f'판단 {n+1}: 지정한 포함 관계·내용 보존·읽기 순서 충족',passed=bool(correct)),
        ])
    return dict(reference_status=reference['status'],checks=checks,
                passed=all(c['passed'] for c in checks),human_approved=False)
