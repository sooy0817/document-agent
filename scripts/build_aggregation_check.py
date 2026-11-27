"""Check provisional annotated scopes against immutable physical cell text."""
import hashlib
import html
import json
from collections import Counter
from pathlib import Path

from doc_understanding.validation.arithmetic import compare_sum

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'data/tree-phase3a-triage'


def main():
    hashes = {}

    def read(p):
        hashes[str(p.relative_to(ROOT))] = hashlib.sha256(p.read_bytes()).hexdigest()
        return json.loads(p.read_text(encoding='utf-8'))

    gold = read(ROOT/'data/tree-phase3a/golden.provisional.json')
    results = read(ROOT/'data/tree-phase3a-repair-v3/results.json')
    tables = {t['table_id']: t for t in results['after']['tables']}
    checks = []
    for t in gold['tables']:
        physical = read(ROOT/f"data/tree-phase3a-repair-v3/tables/p{t['page']}-t{t['index']}-physical.json")
        pcells = {c['cell_id']: c for c in physical['cells']}
        gcells = {c['id']: c for c in t['cells']}
        bindings = tables[t['id']]['bindings']

        def at(row, col):
            matches = [c for c in t['cells'] if c['row']==row and c['column']==col and c['colspan']==1]
            if len(matches)!=1:
                return {'text': None, 'cell_id': None, 'reference_cell_id': None}
            g = matches[0]
            c = pcells.get(bindings.get(g['id']))
            return {'text': c['original_text'] if c else None, 'cell_id': c['cell_id'] if c else None, 'reference_cell_id': g['id']}

        for scope in t['aggregation']:
            targetrow = gcells[scope['target']]['row']
            cols = sorted({c['column'] for c in t['cells'] if c['row']==targetrow and c['column']>0 and c['colspan']==1})
            for col in cols:
                if col in scope['excluded_columns']:
                    continue
                members = [{**at(gcells[m]['row'], col), 'label': gcells[m]['text']} for m in scope['members']]
                total = at(targetrow, col)
                verdict = compare_sum([m['text'] for m in members], total['text'], dash_as_zero=True, blank_as_zero=True)
                verdict['calculation_policy'] = 'User-approved arithmetic policy: dash and bound blank cells contribute zero; missing source stays unknown; raw text unchanged'
                verdict['blank_zero_cell_ids'] = [m['cell_id'] for m in members + [total] if m['cell_id'] and m['text'] is not None and not m['text'].strip()]
                verdict['dash_zero_cell_ids'] = [m['cell_id'] for m in members + [total] if (m['text'] or '').strip() == '-']
                if verdict['status']=='unresolved':
                    verdict['reason']='대시와 확인된 빈칸은 0으로 계산하되 미연결 셀·비표준 숫자는 미확정으로 남깁니다.'
                checks.append({'table_id':t['id'], 'page':t['page'], 'target':scope['target'], 'label':gcells[scope['target']]['text'], 'column_index':col, 'members':members, 'reported_total':total, **verdict, 'scope_status':'provisional; arithmetic match does not validate hierarchy'})
    counts = Counter(c['status'] for c in checks)
    payload = {'dataset':'Namwon development', 'scope_source':'existing manual provisional aggregation annotations, not newly inferred scope', 'counts':dict(counts), 'checks':checks, 'source_sha256':hashes, 'external_calls':0, 'original_values_modified':False}
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'arithmetic.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    esc = html.escape
    names = {'match':'일치','mismatch':'불일치','unresolved':'판정 보류'}
    cards=[]
    for c in checks:
        lines=''.join(f"<tr><td>{esc(m['label'])}</td><td>{esc(m['text'] if m['text'] and m['text'].strip() else ('빈칸 — 검산에서 0 적용' if m['cell_id'] and m['text'] is not None else '원본 셀 미확인'))}</td></tr>" for m in c['members'])
        cards.append(f"<section><h2>p{c['page']} · {esc(c['label'])} · 열 {c['column_index']+1} · {names[c['status']]}</h2><p><a href='../rag-phase3/review/p{c['page']}.png' target='_blank'>원본 페이지 확인</a></p><table><tr><th>합산 대상</th><th>원문 값</th></tr>{lines}</table><p>계산값: <b>{c['sum'] or '계산 보류'}</b> / 원문 합계: <b>{esc(c['reported_total']['text'] or '(미확인)')}</b> / 차이(계산값 − 원문): <b>{c['difference'] or 'N/A'}</b></p><p>{esc(c['reason'])}</p></section>")
    (OUT/'arithmetic.html').write_text("<!doctype html><html lang='ko'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>소계와 합계 검산</title><style>body{font:17px/1.7 system-ui;background:#f2f6f3;color:#18372e;max-width:1000px;margin:auto;padding:24px}section{background:white;padding:24px;margin:20px 0;border:1px solid #cad9d2}h1{font-size:28px}h2{font-size:21px}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:8px;border-bottom:1px solid #ddd}a{color:#176650}</style><h1>소계·합계 직접 검산</h1><p>기존 검토용 집계 범위로 원본 물리 셀의 숫자를 더했습니다. 일치해도 부모 관계나 집계 범위를 자동 확정하지 않습니다. 금리·비율 열은 제외합니다. 사용자 지정에 따라 대시(-)는 검산에서 0으로 계산하며, 원문 표기는 보존합니다. 원본 셀이 확인된 빈칸도 사용자 지정에 따라 검산에서 0으로 계산합니다. 셀 미연결은 미확정으로 남깁니다.</p><p>"+esc(str(dict(counts)))+"</p><a href='index.html'>경로 검토로 돌아가기</a>"+''.join(cards),encoding='utf-8')
    assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in hashes.items())
    print(json.dumps(dict(counts)))


if __name__=='__main__':
    main()
