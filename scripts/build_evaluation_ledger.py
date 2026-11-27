"""Import current experiment outputs into an append-only local history and review UI."""
import json
from pathlib import Path
from doc_understanding.evaluation.ledger import register, encoded, digest, put, verify, RunRecord, ReviewBatch

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/evaluation-ledger'


def read(path):
    return json.loads((ROOT/path).read_text(encoding='utf-8'))


def main():
    def add(title,role,kind,files,metadata):
        return register(OUT,ROOT,{'title':title,'dataset_role':role,'result_kind':kind,'artifacts':files,'metadata':metadata}).run_id

    gold='data/tree-phase3a/golden.provisional.json'
    add('남원 그룹 규칙 v3','development','candidate_tree',['data/tree-phase3a-repair-v3/results.json','data/tree-phase3a-repair-v3/implementation-freeze.json',gold],{'scope':'7 tables; candidate results, not validated facts'})
    v4='data/tree-phase3a-repair-v4/results.json'
    rid=add('남원 행 연결 및 부모 격리 v4','development','candidate_tree',[v4,'data/cross-institution-smoke/freeze.json',gold,'data/tree-phase3a-repair-v4/pytest-full.log','data/tree-phase3a/reviews/user-tree-confirmation.json'],{'scope':'7 tables; 229/233 candidate-view match; quarantine is abstention, not confirmed root'})
    add('남원 합계 검산','development','arithmetic',['data/tree-phase3a-triage/arithmetic.json','docs/AGGREGATION_VERIFICATION_POLICY.md'],{'scope':'40 amount columns; 39 match;1 mismatch; blank and dash zero only in arithmetic'})
    cross='data/cross-institution-smoke/'
    for p in sorted((ROOT/cross).glob('doc*/result.json')):
        d=json.loads(p.read_text(encoding='utf-8'))
        prefix=str(p.parent.relative_to(ROOT)).replace('\\','/')
        add(d['document']['institution']+' '+d['document']['period'],'new_institution_smoke','local_execution',[str(p.relative_to(ROOT)).replace('\\','/'),prefix+'/source-inventory.json',cross+'manifest.json',cross+'freeze.json',prefix+'/candidate-repairs.json'],{'source_sha256':d['document']['sha256'],'institution':d['document']['institution'],'native_pages':d['native_text_pages'],'pages':d['pages'],'evaluated_pages':d['evaluated_pages'],'accuracy':None,'prior_exposure_after_this_run':True})
    unju=add('운주 p17 검산 주석','new_institution_smoke','provisional_arithmetic',[cross+'unju-provisional-arithmetic.json',cross+'manifest.json'],{'scope':'3 manually image-read scopes × 2 years; provisional'})
    if (OUT/'pytest-current.log').exists():
        add('기록부 구현 회귀 검증','synthetic_and_regression','software_tests',['data/evaluation-ledger/pytest-current.log','src/doc_understanding/evaluation/ledger.py','tests/test_evaluation_ledger.py','docs/ADR/016-evaluation-ledger.md'],{'scope':'Software regression; not document semantic accuracy. Exact test results are preserved in the log.'})
    g=read(gold)
    tables={t['id']:t for t in g['tables']}
    cells={c['id']:c for t in g['tables'] for c in t['cells']}
    result=read(v4)
    cases=[]

    def image(page):
        p=ROOT/f'data/rag-phase3/review/p{page}.png'
        b=p.read_bytes()
        rel='assets/'+digest(b)+'.png'
        put(OUT/rel,b)
        return rel

    def case(body):
        body['case_id']=digest(encoded(body))[:24]
        body['status']='review_required'
        cases.append(body)

    def labels(path):
        return ' → '.join(cells.get(x,{}).get('text',x) for x in path) or '(연결 없음)'
    for t in result['tables']:
        for i in t['issues']:
            if i['code']!='CELL_PATH':
                continue
            c=cells[i['target']]
            case({'run_id':rid,'kind':'정답 영역·경로 확인','title':f"남원 p{t['page']} · {i['raw_value']}",'question':'강조한 값의 행·열 문맥과 정답 영역이 원본에 맞는지 확인해 주세요. 숫자를 고치는 요청이 아닙니다. 독립 비율 행은 합계 열에 속하는지 표 전체의 B/A인지 확인합니다.','expected':f"행: {labels(i['expected']['row'])}\n열: {labels(i['expected']['column'])}",'predicted':f"행: {labels(i['predicted']['row'])}\n열: {labels(i['predicted']['column'])}",'image':image(t['page']),'viewbox':tables[t['table_id']]['bbox'],'highlight':c['bbox'],'source_cell':i['target'],'source_artifact_sha256':digest((ROOT/gold).read_bytes())})
    for table in result['traces']:
        t=next(x for x in result['tables'] if x['table_id']==table['table'])
        inverse={v:k for k,v in t['bindings'].items()}
        for tr in table['trace']:
            if tr['action']!='QUARANTINE':
                continue
            child=inverse.get(tr['child_cell_id'])
            if not child:
                continue
            c=cells[child]
            parent=inverse.get(tr['previous_parent_cell_id'])
            case({'run_id':rid,'kind':'親 관계 기권 확인'.replace('親','부모'),'title':f"남원 p{t['page']} · {c['text']}",'question':'이 항목이 아래 기존 부모에 실제로 속하는지 원본을 보고 판단해 주세요. 동의는 의심 관계를 미확정으로 분리한 판단에 대한 동의이며 정상 루트 확정은 아닙니다.','expected':'현재 판단: 근거 부족으로 부모 연결을 미확정으로 분리','predicted':'기존 부모: '+(cells[parent]['text'] if parent else '표 안에서 대응되지 않는 부모'),'image':image(t['page']),'viewbox':tables[t['table_id']]['bbox'],'highlight':c['bbox'],'source_cell':child,'source_artifact_sha256':digest((ROOT/v4).read_bytes())})
    u=read(cross+'unju-provisional-arithmetic.json')
    original=ROOT/(cross+'doc4-example.png')
    b=original.read_bytes()
    asset='assets/'+digest(b)+'.png'
    put(OUT/asset,b)
    for group in u['annotation']['groups']:
        checks=[c for c in u['checks'] if c['label']==group['label']]
        case({'run_id':unju,'kind':'신규 정답 집계 범위 확인','title':'운주 p17 · '+group['label'],'question':'표에서 이 소계·합계에 포함할 행이 맞는지 확인해 주세요. 아래 식에 빠진 행이나 중복 합산이 없는지도 봐주세요. 차이가 있어도 원문 숫자는 수정하지 않습니다.','expected':'\n'.join(c['period']+': '+' + '.join(m['text'] or '(빈칸→0)' for m in c['members'])+' = '+str(c['sum']) for c in checks),'predicted':'\n'.join(c['period']+': 원문 '+c['total']['text']+' / 차이 '+str(c['difference']) for c in checks),'image':asset,'viewbox':None,'highlight':None,'source_cell':checks[0]['total']['cell_id'],'source_artifact_sha256':digest((ROOT/(cross+'unju-provisional-arithmetic.json')).read_bytes())})
    queue={'schema_version':'evaluation-queue/1.0','cases':cases,'note':'Review events do not mutate gold. No claims of independent validation before import.'}
    qb=encoded(queue)
    qh=digest(qb)
    put(OUT/'queues'/f'{qh}.json',qb)
    runs=[json.loads(p.read_text(encoding='utf-8')) for p in sorted((OUT/'runs').glob('*.json'))]
    reviews=[json.loads(p.read_text(encoding='utf-8')) for p in sorted((OUT/'reviews').glob('*.json'))] if (OUT/'reviews').exists() else []
    data={'runs':runs,'queue':queue,'queue_sha256':qh,'review_events':reviews}
    (OUT/'catalog.json').write_bytes(encoded(data))
    template=(ROOT/'scripts/evaluation_ledger_template.html').read_text(encoding='utf-8')
    (OUT/'index.html').write_text(template.replace('__DATA__',json.dumps(data,ensure_ascii=False).replace('<','\\u003c')),encoding='utf-8')
    for model in (RunRecord,ReviewBatch):
        (OUT/f'{model.__name__}.schema.json').write_bytes(encoded(model.model_json_schema()))
    print(json.dumps({'runs':verify(OUT),'review_cases':len(cases),'queue_sha256':qh}))


if __name__=='__main__':
    main()
