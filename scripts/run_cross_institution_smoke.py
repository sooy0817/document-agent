"""Frozen local pipeline smoke test; no accuracy claims without independent gold."""
import os
import argparse
import hashlib
import json
import random
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'data/cross-institution-smoke'
SOURCE = Path(os.environ.get("DISCLOSURE_SOURCE_DIR", "data/disclosure-source"), '연말결산')


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def save(name,obj):
    p=OUT/name
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')


def prepare():
    if (OUT/'manifest.json').exists():
        raise SystemExit('Existing manifest preserved')
    freeze={str(p.relative_to(ROOT)):sha(p) for folder in ['src','tests','scripts'] for p in (ROOT/folder).rglob('*.py')}
    save('freeze.json',{'files':freeze,'seed':20261019,'external_calls':0,'page_sampling':'at most 6 evenly spaced pages including first and last, fixed before inference','policy':'Namwon/ Ganggu development; Nampyeong reserved; one institution-period group per selection; no tuning after execution'})
    rng=random.Random(20261019)
    # Filename metadata selection only, never open final holdout files.
    candidates=[]
    for year,kind,needle in [('2023','audit','외부'),('2024','statement','재무상태표'),('2025','disclosure','정기')]:
        paths=sorted(p for p in (SOURCE/year).rglob('*.pdf') if needle in p.name and not any(n in str(p) for n in ['남평','남원','강구']))
        rng.shuffle(paths)
        candidates.append((year,kind,paths))
    chosen=[]
    for year,kind,paths in candidates:
        for p in paths:
            institution=p.parent.name
            if institution in {c['institution'] for c in chosen}:
                continue
            h=sha(p)
            if h in {c['sha256'] for c in chosen}:
                continue
            chosen.append({'source_path':str(p),'institution':institution,'period':year,'document_type':kind,'format_family':'unknown_until_source_review','sha256':h,'leakage_group':institution+'/'+year,'metadata_provenance':'directory and filename; not independently verified','duplicate_review':'exact SHA dedup; same institution-period excluded; cross-document content duplicates not independently audited'})
            break
    save('manifest.json',{'seed':20261019,'role':'new-institution local smoke test, not finalized generalization benchmark','freeze_sha256':sha(OUT/'freeze.json'),'documents':chosen,'nampyeong_opened':False,'candidate_counts':[{'year':y,'type':k,'count':len(ps)} for y,k,ps in candidates]})
    print(json.dumps(chosen,ensure_ascii=False),flush=True)


def execute():
    import pymupdf
    from doc_understanding.parser.physical.runner import evaluate_pdf
    from doc_understanding.domain.models import DocumentIR
    from doc_understanding.parser.physical.physical_models import PhysicalTableIR
    from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer
    from doc_understanding.agents.semantic_resolver.artifacts import save_run
    from doc_understanding.agents.semantic_resolver.models import ResolverConfig
    from doc_understanding.agents.semantic_resolver.group_context import propose_intervals
    from doc_understanding.agents.semantic_resolver.path_repair import repair_paths
    from doc_understanding.parser.structure.models import StructuredTableIR
    freeze=json.loads((OUT/'freeze.json').read_text(encoding='utf-8'))
    assert all(sha(ROOT/p)==h for p,h in freeze['files'].items())
    manifest=json.loads((OUT/'manifest.json').read_text(encoding='utf-8'))
    for idx,item in enumerate(manifest['documents']):
        if (OUT/f'doc{idx}/result.json').exists():
            continue
        start=time.monotonic()
        p=Path(item['source_path'])
        assert '남평' not in str(p) and sha(p)==item['sha256']
        try:
            with pymupdf.open(p) as pdf:
                counts=[len(page.get_text().strip()) for page in pdf]
                n=len(pdf)
                pages=sorted({1+round(i*(n-1)/5) for i in range(6)})
            save(f'doc{idx}/source-inventory.json',{'pages':n,'native_chars_by_page':counts,'selected_pages':pages,'selected_before_table_inference':True})
            print(f'Start {idx}: {item["institution"]}, {n} pages, physical pages {pages}',flush=True)
            run,summary=evaluate_pdf(p,OUT/f'doc{idx}/physical',pages,False,label='unreviewed_new_institution_smoke')
            doc=DocumentIR.model_validate_json((run/'document-ir.json').read_bytes())
            physical=tuple(PhysicalTableIR.model_validate_json(f.read_bytes()) for f in sorted((run/'physical').glob('page-*-table-*.json')))
            structures=tuple(SemanticTableAnalyzer().analyze(t,physical) for t in physical)
            sr,semantic=save_run(doc,physical,structures,OUT/f'doc{idx}/semantic',mode='rule_only',provider=None,config=ResolverConfig(allow_external=False))
            overlay=json.loads((sr/'semantic-overlay.json').read_text(encoding='utf-8'))
            nodecell={f['node_id']:f['source_cell_ids'][0] for f in overlay['features'] if len(f['source_cell_ids'])==1}
            parents={nodecell[n['semantic_node_id']]:nodecell.get(n['semantic_parent_id'],n['semantic_parent_id']) for n in overlay['table_nodes'] if n['semantic_node_id'] in nodecell and n['semantic_parent_id']}
            repairs=[]
            for t,s in zip(physical,structures):
                pd,sd=t.model_dump(mode='json'),s.model_dump(mode='json')
                proposals=propose_intervals(pd,sd)
                candidate=dict(parents)
                for rel in proposals['proposals']:
                    candidate[rel['child_cell_id']]=rel['parent_cell_id']
                refined,edges,trace=repair_paths(pd,sd,candidate)
                StructuredTableIR.model_validate(refined)
                repairs.append({'table_id':t.table_id,'group_proposals':proposals,'trace':trace,'candidate_parents':{k:v for k,v in edges.items() if k in {c.cell_id for c in t.cells}},'structured_candidate':refined,'status':'review_required; geometry candidates not validated semantic patches'})
            save(f'doc{idx}/candidate-repairs.json',repairs)
            result={'document':item,'pages':n,'native_text_pages':sum(c>0 for c in counts),'evaluated_pages':pages,'physical_run':str(run),'semantic_run':str(sr),'physical_summary':summary,'semantic_summary':semantic,'repair_actions':len([a for r in repairs for a in r['trace']]),'independent_gold':False,'tree_accuracy':None,'aggregation_accuracy':None,'external_calls':0,'seconds':time.monotonic()-start,'source_unchanged':sha(p)==item['sha256'],'status':'completed_local_smoke'}
        except Exception as e:
            result={'document':item,'status':'failed','error':type(e).__name__+': '+str(e),'seconds':time.monotonic()-start,'external_calls':0}
        save(f'doc{idx}/result.json',result)
        print(f'Finished {idx}: {result["status"]}',flush=True)
    assert all(sha(ROOT/p)==h for p,h in freeze['files'].items())


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['prepare','execute'])
    args=parser.parse_args()
    prepare() if args.mode=='prepare' else execute()
