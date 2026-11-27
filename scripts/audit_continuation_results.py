"""Post-run reference checks. Never feeds reference labels back to inference."""
import argparse
import html
import json
from pathlib import Path


def read(p): return json.loads(p.read_text(encoding='utf8'))


def audit(prepared,run):
    refs=read(prepared/'reference.json')['cases']
    records=[]
    for ref in refs:
        r=read(run/(ref['id']+'.json'));s=r['state'];checks=[]
        for check in ref['scope_checks']:
            source=next(i for i,c in s['evidence']['registry'].items() if c.get('kind')=='cell'
                and c['page']==check['source_page'] and c['table_id'].endswith('/'+check['source_fragment'])
                and i.endswith('/'+check['source_cell'])) if any(c.get('kind')=='cell'
                and c['page']==check['source_page'] and c['table_id'].endswith('/'+check['source_fragment'])
                and i.endswith('/'+check['source_cell']) for i,c in s['evidence']['registry'].items()) else None
            target=s['pair']['target_table_id']
            required={target+'/'+i for i in check['required_targets']}
            forbidden={target+'/'+i for i in check['forbidden_targets']}
            def evaluate(scopes):
                selected={t for scope in scopes if source in scope['source_cell_ids']
                          and scope['kind']==check['kind'] for t in scope['target_cell_ids']}
                # Renaming a forbidden row-label relation to 'column header' must
                # not make an erroneous application disappear from the audit.
                any_axis={t for scope in scopes if source in scope['source_cell_ids'] for t in scope['target_cell_ids']}
                return dict(missing=sorted(required-selected),wrong=sorted(forbidden & any_axis),
                            selected=sorted(selected),passed=required<=selected and not forbidden & any_axis)
            baseline=s['rule_baseline'].get('link')
            baseline_scopes=[]
            if baseline:
                for path in baseline['paths']:
                    for kind,key in [('header_inheritance','column_source_cell_ids'),('row_group_continuation','row_source_cell_ids')]:
                        for cid in path[key]:
                            baseline_scopes.append(dict(kind=kind,source_cell_ids=[cid],target_cell_ids=[path['cell_id']]))
            checks.append(dict(reference=check,source_cell_id=source,
                rule=evaluate(baseline_scopes),proposal=evaluate(s.get('proposal',{}).get('scopes',[])),
                final=evaluate(s['final']['scopes'])))
        records.append(dict(id=ref['id'],final_relation=s['final']['decision'],
            pair_match=ref['expected']==s['final']['decision'],scope_checks=checks,
            checked_scope_failures=[i for i,c in enumerate(checks) if not c['final']['passed']],
            review_status='source_reviewed_draft_not_human_approved'))
    result=dict(records=records,notice='Selected predeclared scope checks only, not full table/cell Gold. Forbidden applications are checked across both axis kinds. No inference input is modified.')
    with (run/'scope-audit.json').open('x',encoding='utf8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    content=(run/'comparison.html').read_text(encoding='utf8')
    for r in records:
        note=('선정한 적용 범위 검사에서 오류/누락이 남았습니다.' if r['checked_scope_failures'] else
              '선정한 적용 범위 검사에서 모순이 발견되지 않았습니다. 전체 셀 정확도 검증은 아닙니다.')
        evidence_html='<div class="warning"><b>원본 참조 대조: '+note+'</b><details><summary>선정한 범위의 규칙·제안·최종 비교</summary><pre>'+html.escape(json.dumps(r,ensure_ascii=False,indent=2))+'</pre></details></div>'
        content=content.replace(f'<section id="{r["id"]}">',f'<section id="{r["id"]}">'+evidence_html,1)
        record=read(run/(r['id']+'.json'));s=record['state'];p=s['pair']
        extra=[n for n in s['evidence']['pages'] if n not in (p['source_page'],p['target_page'])]
        if extra:
            # Expanded evidence must also be inspectable, not only buried in JSON.
            extra_html='<details open><summary>추가 수집한 원본 문맥</summary><div class="images">'
            import pymupdf
            job=Path(read(prepared/'manifest.json')['job'])
            with pymupdf.open(job/'input.pdf') as pdf:
                for n in extra:
                    box=pdf[n-1].rect
                    extra_html+=f'<div><b>추가 원본 {n}쪽</b><div class="sheet" data-page="{n}"><a href="page-{n}.png" target="_blank"><img loading="lazy" src="page-{n}.png" alt="추가 원본 {n}쪽"></a><svg viewBox="0 0 {box.width} {box.height}"></svg></div></div>'
            extra_html+='</div></details>'
            start=content.index(f'<section id="{r["id"]}">');end=content.index('</section>',start)
            content=content[:end]+extra_html+content[end:]
    content=content.replace('<h1>계속표 관계: 규칙과 원본 이미지 판단 비교</h1>',
        '<h1>계속표 관계: 규칙과 원본 이미지 판단 비교</h1><p><a href="comparison.html">수정하지 않은 실행 보고서</a> · <a href="scope-audit.json">실행 후 원본 참조 대조</a></p>')
    with (run/'comparison-reviewed.html').open('x',encoding='utf8') as f:f.write(content)
    print(json.dumps(dict(cases=len(records),pair_matches=sum(r['pair_match'] for r in records),
        cases_with_scope_failures=[r['id'] for r in records if r['checked_scope_failures']]),ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--prepared',type=Path,required=True);p.add_argument('--run',type=Path,required=True)
    a=p.parse_args();audit(a.prepared,a.run)
