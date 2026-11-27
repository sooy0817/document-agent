"""Reproduce linking over a frozen explorer snapshot; no extraction or API calls.

Example: python scripts/eval_logical_links.py --job <folder> --output <new-folder>
"""
from doc_understanding.services import documents
from doc_understanding.review import error_basket
from doc_understanding.web import app
import argparse
import hashlib
import html
import json
import shutil
import sys
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]

from doc_understanding.domain.pipeline_contracts import CanonicalDocument
from doc_understanding.parser.assembly.document import validate_document
from doc_understanding.parser.assembly.nested_content import build_nested_content
from doc_understanding.parser.relations.logical_tables import build_logical_links


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path,value):
    with path.open('x',encoding='utf8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2)


def run(job,out):
    out.mkdir(parents=True,exist_ok=False)
    before=CanonicalDocument.model_validate_json((job/'document.json').read_bytes())
    links=build_logical_links(before.document_ir,before.physical_tables)
    after=before.model_copy(update={'logical_table_links':links})
    validate_document(after)
    basket=ROOT/'work/document-explorer/error-basket'
    items=[i for i in error_basket.list_items(basket) if i['job_id']==job.name]
    original_files=[job/'input.pdf',job/'document.json',job/'nested.json',job/'result.json']
    original_files += [p for i in items for p in (basket/i['id']).iterdir()
                       if p.name=='snapshot.json' or p.name.startswith('event-')]
    hashes={str(p):sha(p) for p in original_files}
    supplement_paths=[str(error_basket.supplement_context(basket,i['id'],job)) for i in items]
    # A separate saved review makes the before/after available without replacing it.
    destination=job.parent/uuid.uuid4().hex;destination.mkdir()
    shutil.copy2(job/'input.pdf',destination/'input.pdf')
    documents.export_document(after,build_nested_content(after),destination,'review_required')
    review=json.loads((job/'review.json').read_text(encoding='utf8')) if (job/'review.json').exists() else {}
    review.update(name=review.get('name','문서')+' · 표 연결 개선 비교',
                  purpose='development_feedback_regression',status='human_review_pending',
                  baseline_job_id=job.name,source_code_unchanged=False,
                  actual_api_calls=0,estimated_cost_usd=0,
                  metrics_notice='연결 후보 비교입니다. 원시 IR은 동일하며 전체 표 Gold 또는 독립 일반화 평가는 아닙니다.')
    if 'document' in review:review['document']['split']='development_feedback'
    write(destination/'review.json',review)
    cells={c.cell_id:c for t in before.physical_tables for c in t.cells}
    old={m.cell_id:(m,s) for s in before.table_structure for m in s.cell_mappings}
    comparisons=[]
    for link in links:
        samples=[]
        for path in link.paths:
            original=old.get(path.cell_id)
            previous=dict(row_path=[],column_path=[],state='no_value_mapping')
            if original:
                m,s=original;nodes={n.node_id:n.label_raw for n in s.row_tree+s.column_tree}
                previous=dict(row_path=[nodes[n] for n in m.row_path],column_path=[nodes[n] for n in m.column_path],state=m.evidence_state)
            samples.append(dict(cell_id=path.cell_id,value_raw=cells[path.cell_id].original_text,
                before=previous,after=dict(row_path=[cells[c].original_text for c in path.row_source_cell_ids],
                    column_path=[cells[c].original_text for c in path.column_source_cell_ids],
                    state=path.state,reasons=path.reasons),source_path=path.model_dump(mode='json')))
        comparisons.append(dict(link=link.model_dump(mode='json'),samples=samples))
    preserved=all(sha(Path(p))==h for p,h in hashes.items())
    raw_same=all(getattr(before,k)==getattr(after,k) for k in
                 ('document_ir','physical_tables','table_structure','cell_mappings','contents'))
    assert preserved and raw_same
    report=dict(baseline_job_id=job.name,after_job_id=destination.name,
        after_url=f'http://127.0.0.1:8766/?job={destination.name}',
        links=len(links),path_candidates=sum(len(l.paths) for l in links),
        raw_ir_unchanged=raw_same,original_files_unchanged=preserved,
        original_hashes=hashes,evidence_supplements=supplement_paths,
        comparisons=comparisons,api_calls=0,cost_usd=0,
        disclaimer='연결 후보의 구조 검사입니다. 사람 승인 전체 셀 정답·범용 정확도·RAG 답변 정확도가 아닙니다.')
    write(out/'results.json',report)
    css='body{font:16px/1.65 system-ui,"Malgun Gothic";color:#20303b;background:#f5f6f8;margin:0}main{max-width:1400px;margin:auto;padding:30px}section{background:white;border:1px solid #ccd4dc;padding:20px;margin:20px 0}.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}.grid>div{min-width:0}img{width:100%;height:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:500px;overflow:auto;background:#f2f4f7;padding:12px;font-size:12px}a{color:#146198;overflow-wrap:anywhere}td,th{border:1px solid #ccd4dc;text-align:left;vertical-align:top;padding:8px}table{border-collapse:collapse;width:100%;font-size:13px}.warn{background:#fff3d6;padding:18px}details{margin:15px 0}@media(max-width:850px){.grid{grid-template-columns:1fr}}'
    esc=html.escape
    parts=['<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>계속표·분리 헤더 연결 전후</title><style>'+css+'</style><main><h1>담기 보완 · 계속표·분리 헤더 연결 전후</h1>',
       f'<p><a href="{report["after_url"]}">개선 결과를 기존 검토 화면에서 열기</a> · <a href="http://127.0.0.1:8766/?job={job.name}">동결된 이전 결과</a> · <a href="results.json">실제 비교 JSON 전체</a></p>',
       f'<div class="warn">{len(links)}개 표 조각 연결 후보. 원시 IR·셀·기존 매핑은 변경하지 않았습니다. 새 경로는 CanonicalDocument.logical_table_links에 별도로 저장합니다. 검토 화면은 이 후보를 표시하며, RAG 답변 생성 정책은 이번 변경에 포함하지 않습니다. 후보 경로 생성 수는 정답률이 아닙니다.</div>',
       '<section><h2>담기 보완</h2><p>원본 담기 snapshot과 메모를 보존하고 연결 증거만 별도 supplement로 추가했습니다. 내보내기 ZIP의 record.json에 evidence_supplements가 포함됩니다. 새로 담을 때는 부모 ID 대응과 자식 표 JSON, 앞 페이지 연결 근거가 함께 들어갑니다.</p>']
    for i in items:
        rec=error_basket.detail(basket,i['id']);latest=rec['evidence_supplements'][-1]
        parts.append(f'<p>{i["page"]}쪽 · 기존 중첩 연결 {len(rec["snapshot"].get("nested_context",[]))} → 보완 증거 {len(latest["nested_context"])}개 · <a href="{(basket/i["id"]).as_uri()}">보존 기록</a></p>')
    parts.append('</section>')
    import pymupdf
    with pymupdf.open(job/'input.pdf') as pdf:
        pages={t.page_number for t in after.physical_tables if any(t.table_id in (l.source_table_id,l.target_table_id) for l in links)}
        for p in pages:pdf[p-1].get_pixmap(matrix=pymupdf.Matrix(1.2,1.2)).save(out/f'p{p:02}.png')
    tables={t.table_id:t for t in before.physical_tables}
    for comp in comparisons:
        l=comp['link'];a=tables[l['source_table_id']].page_number;b=tables[l['target_table_id']].page_number
        parts.append(f'<section><h2>{a}쪽 → {b}쪽 · {esc(l["kind"])}</h2><p><a href="{report["after_url"]}&page={b}">이 페이지 검토하기</a> · 상태: 후보 / 의미 검증 전</p><div class="grid"><div><h3>근거가 있는 원본 {a}쪽</h3><a href="p{a:02}.png"><img loading="lazy" src="p{a:02}.png"></a></div><div><h3>연결 대상 원본 {b}쪽</h3><a href="p{b:02}.png"><img loading="lazy" src="p{b:02}.png"></a></div></div><details open><summary>셀별 실제 경로 전후</summary><table><tr><th>셀·원문</th><th>기존 경로</th><th>연결 후보</th><th>보류 이유</th></tr>')
        for s in comp['samples']:
            old=s['before'];new=s['after']
            path=lambda v:esc(' → '.join(v['row_path'])+' / '+' → '.join(v['column_path']))
            parts.append('<tr><td>'+esc(s['cell_id'].split('/')[-1])+'<br>'+esc((s['value_raw'] or '')[:100])+'</td><td>'+path(old)+'</td><td>'+path(new)+'</td><td>'+esc(new['state']+' / '+', '.join(new['reasons']))+'</td></tr>')
        parts.append('</table></details><details><summary>원본 셀 ID까지 포함한 실제 연결 JSON</summary><pre>'+esc(json.dumps(l,ensure_ascii=False,indent=2))+'</pre></details></section>')
    parts.append('<section><h2>해석 범위와 남은 문제</h2><p>6쪽처럼 명시적 열 제목을 확보하지 못한 경우 열 축은 비워 둡니다. 여러 행에 걸친 공통 값은 MULTIPLE_ROW_SCOPES로 보류합니다. 5쪽 내부 표 미복원, 36쪽 물리 경계, 도식/양식 처리 및 모든 표의 의미 판정은 이번 수정 범위가 아닙니다. 숫자·연도 헤더, 다단 분리 헤더 등 현재 보수적 조건에 맞지 않는 연결은 지원하지 않습니다.</p><p>원본·기존 결과 해시 동일. API 호출 0회 / 비용 $0. 이 문서는 개발 피드백 자료이며 새 Holdout 결과가 아닙니다.</p></section></main></html>')
    (out/'comparison.html').write_text(''.join(parts),encoding='utf8')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();r=run(args.job.resolve(),args.output.resolve())
    print(json.dumps({k:r[k] for k in ('after_url','links','path_candidates','raw_ir_unchanged','original_files_unchanged')},ensure_ascii=False))
