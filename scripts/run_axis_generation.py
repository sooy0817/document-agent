"""Explicit source-grounded generation. Default offline; immutable output only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from run_axis_graph import ROOT, read, write, source_image
from doc_understanding.agents.axis_review.axis_resolver import AxisRegion
from doc_understanding.agents.axis_review.axis_generation import AxisGenerationAdapter, GeneratedStructure, build_generation_graph


def run(source, output, *, allow_external=False, vision=False, max_calls=1,
        replay=None, region_index=None, prompt_version='v1'):
    sources=sorted(source.glob('region-*-source.json'))
    if region_index is not None: sources=[sources[region_index]]
    if not sources: raise ValueError('NO_SOURCE_REGIONS')
    output.mkdir(parents=True,exist_ok=False)
    hashes={str(p.resolve()):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source.iterdir() if p.is_file()}
    baseline=read(source/'baseline-structured.json'); adapter=AxisGenerationAdapter(
        allow_external=allow_external,max_calls=max_calls,prompt_version=prompt_version)
    summaries=[]; ledger=[]
    for path in sources:
        name=path.name.removesuffix('-source.json'); region=AxisRegion.model_validate(read(path))
        tables=[t for t in baseline if t['table_id'] in {c.table_id for c in region.cells}]
        resolver=None
        if replay:
            saved=read(replay/f'{name}-graph.json')
            if saved['source_hash']!=hashlib.sha256(region.model_dump_json().encode()).hexdigest():
                raise ValueError('REPLAY_SOURCE_HASH_MISMATCH')
            def resolver(r, saved=saved):
                return (GeneratedStructure.model_validate(saved['generated']) if saved.get('generated') else None,
                        {'status':'replayed','actual_api_calls':0,'estimated_cost_usd':0})
        elif allow_external:
            image=source_image(source,name).read_bytes() if vision else None
            def resolver(r,image=image): return adapter.resolve(r,image)
        graph=build_generation_graph(region,tables,resolver=resolver)
        state=graph.invoke({},config={'recursion_limit':12})
        write(output/f'{name}-graph.json',state)
        write(output/f'{name}-structured-review.json',state['structured'])
        write(output/f'{name}-review-record.json',state['review_record'])
        if not (output/'graph.mmd').exists():
            (output/'graph.mmd').write_text(graph.get_graph().draw_mermaid(),encoding='utf-8')
        record=state.get('model_record')
        if record: ledger.append(record)
        audit=state.get('generation_validation') or {}; links=audit.get('checked_links',[])
        summaries.append(dict(region_id=region.id,source=path.name,result=name+'-graph.json',
            route_reason=state['route_reason'],model_invocations=state['model_invocations'],
            generated=len(links),structurally_consistent=sum(c['status']=='structurally_consistent_candidate' for c in links),
            rejected=sum(c['status']=='rejected' for c in links),
            legacy_supported=sum(c['status']=='supported_candidate' for c in audit.get('legacy_validation',{}).get('checked_links',[]))))
    unchanged=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in hashes.items())
    manifest=dict(schema_version='axis-generation-run/1.0',source=str(source.resolve()),regions=summaries,
        original_hashes=hashes,originals_unchanged=unchanged,actual_api_attempts=adapter.calls,
        completed_calls=sum(r.get('status')=='completed' for r in ledger),
        estimated_cost_usd=sum(r.get('estimated_cost_usd') or 0 for r in ledger),
        unknown_cost_calls=sum(r.get('estimated_cost_usd') is None for r in ledger),
        semantic_verified=False,facts_published=0,training_performed=False)
    write(output/'manifest.json',manifest)
    if not unchanged: raise RuntimeError('SOURCE_CHANGED')
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--allow-external',action='store_true');p.add_argument('--vision',action='store_true')
    p.add_argument('--max-calls',type=int,default=1);p.add_argument('--replay',type=Path)
    p.add_argument('--region-index',type=int)
    p.add_argument('--prompt-version',choices=['v1','explicit-v2'],default='v1')
    p.add_argument('--env-file',type=Path,help='Read OPENAI_API_KEY only; never print it')
    a=p.parse_args()
    if a.env_file and a.allow_external:
        for line in a.env_file.read_text(encoding='utf-8-sig').splitlines():
            if line.strip().startswith('OPENAI_API_KEY='):
                os.environ.setdefault('OPENAI_API_KEY',line.split('=',1)[1].strip().strip('\"\''))
    args=vars(a);args.pop('env_file')
    result=run(**args)
    print(json.dumps({k:v for k,v in result.items() if k!='original_hashes'},ensure_ascii=False))
