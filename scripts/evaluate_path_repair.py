import json
from pathlib import Path
from doc_understanding.evaluation.parser.tree_accuracy import read, evaluate_table, micro, digest
from doc_understanding.agents.semantic_resolver.path_repair import repair_paths
from doc_understanding.parser.structure.models import StructuredTableIR

ROOT = Path(__file__).resolve().parents[1]


def main():
    out = ROOT/'data/tree-phase3a-repair-v4'
    out.mkdir(exist_ok=True)
    gold_path = ROOT/'data/tree-phase3a/golden.provisional.json'
    h = digest(gold_path)
    gold = read(gold_path)
    previous = read(ROOT/'data/tree-phase3a-repair-v3/results.json')['after']
    results, traces, changes = [], [], []
    for g, b in zip(gold['tables'], previous['tables']):
        stem = f"p{g['page']}-t{g['index']}"
        p = read(ROOT/f'data/tree-phase3a-repair-v3/tables/{stem}-physical.json')
        s = read(ROOT/f'data/tree-phase3a-repair-v3/tables/{stem}-structured.json')
        bindings = b['bindings']
        parents = {bindings[c]: bindings.get(v,v) for c,v in b['predicted_row_parents'].items() if c in bindings and v}
        repaired, edges, trace = repair_paths(p,s,parents)
        StructuredTableIR.model_validate(repaired)
        a = evaluate_table(g,p,repaired,edges)
        results.append(a)
        traces.append({'table':g['id'],'trace':trace})
        for code in ['ROW_PATH','CELL_PATH','EVIDENCE']:
            old = {i['target'] for i in b['issues'] if i['code']==code}
            new = {i['target'] for i in a['issues'] if i['code']==code}
            changes.append({'table':g['id'],'code':code,'recovered':len(old-new),'regressed':len(new-old),'remaining':len(new)})
        (out/f'{stem}-structured.json').write_text(json.dumps(repaired,ensure_ascii=False,indent=2),encoding='utf-8')
    report = {'before':previous['micro'],'candidate_view':micro(results),'tables':results,'changes':changes,'traces':traces,'warning':'Quarantined relationships are abstentions, not confirmed roots. Candidate-view scores are not validated accuracy.','gold_unchanged':digest(gold_path)==h,'gold_sha256':h,'external_calls':0}
    (out/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ['before','candidate_view','changes']},ensure_ascii=False))


if __name__=='__main__':
    main()
