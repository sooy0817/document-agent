"""Replay stored rules/model responses through stricter validation. No API calls."""
import argparse,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from doc_understanding.agents.axis_review.axis_resolver import AxisRegion, AxisProposal
from doc_understanding.agents.axis_review.axis_validation import audit_region, validated_views, audit_mapping_coverage

def read(p):return json.loads(p.read_text(encoding='utf-8'))
def write(p,x):
    with p.open('x',encoding='utf-8') as f:json.dump(x,f,ensure_ascii=False,indent=2)
def replay(source,output):
    output.mkdir(parents=True,exist_ok=False)
    paths=[p for p in source.rglob('*') if p.is_file()]
    hashes={str(p.resolve()):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    manifest=read(source/'manifest.json');baseline=read(source/'baseline-structured.json');rows=[]
    write(output/'baseline-structured.json',baseline)
    for i,r in enumerate(manifest['regions']):
        region=AxisRegion.model_validate(read(source/r['source']))
        write(output/f'region-{i:03}-source.json',region.model_dump(mode='json'))
        record=dict(id=region.id,source=r['source'],source_image=str((source/r['image']).resolve()),modes={})
        for mode,m in r['modes'].items():
            saved=read(source/m['result']);proposal=AxisProposal.model_validate(saved['proposal'])
            audit=audit_region(region,proposal)
            name=f'region-{i:03}-{mode}'
            write(output/(name+'.json'),dict(proposal=saved['proposal'],previous_checked=saved['checked'],
                checked=[c.model_dump(mode='json') for c in audit.checked_links],validation=audit.model_dump(mode='json'),
                baseline_coverage=[c.model_dump(mode='json') for c in audit_mapping_coverage(region,baseline)],
                saved_call_status=(saved.get('call') or {}).get('status'),replay_only=True,api_calls=0))
            views=validated_views(region,audit,[t for t in baseline if t['table_id'] in {c.table_id for c in region.cells}])
            write(output/(name+'-structured.json'),[v.model_dump(mode='json') for v in views])
            record['modes'][mode]=dict(before=sum(c['status']=='supported_candidate' for c in saved['checked']),
                after=sum(c.status=='supported_candidate' for c in audit.checked_links),
                cells=len(audit.cells),nonblank=sum(bool(c.text_raw.strip()) for c in audit.cells),
                unaccounted=sum(c.disposition=='unaccounted' for c in audit.cells),
                unresolved_role=sum('ROLE_UNRESOLVED' in c.issues for c in audit.cells),result=name+'.json')
        rows.append(record)
    result=dict(source_run=str(source.resolve()),page=manifest['page'],regions=rows,original_hashes=hashes,
        originals_unchanged=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in hashes.items()),
        api_calls=0,estimated_cost_usd=0,semantic_verified=False,facts_published=0)
    write(output/'manifest.json',result);return result
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();r=replay(a.source,a.output);print(json.dumps(r['regions'],ensure_ascii=False))
