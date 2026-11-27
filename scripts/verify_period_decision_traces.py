"""Read-only local Langfuse verification; never rerun inference."""
import json
from pathlib import Path
import httpx
from run_axis_judge import ROOT,read,write
from doc_understanding.runtime.telemetry import load_config
from doc_understanding.rag.observability.judge import verify_scores

if __name__=='__main__':
    base=ROOT/'reports/period-decisions-20261030'
    config=load_config(ROOT/'infra/langfuse/client.env.local')
    auth=(config['public_key'],config['secret_key']);url=config['base_url'].rstrip('/')
    scores=[]
    for page in range(1,101):
        response=httpx.get(url+'/api/public/scores',params=dict(limit=100,page=page),auth=auth,timeout=10)
        response.raise_for_status();body=response.json();scores+=body['data']
        if page>=body.get('meta',{}).get('totalPages',1):break
    records=[]
    for path in sorted(base.glob('*-run-01/region-???.json')):
        saved=read(path);tid=saved['trace_id']
        response=httpx.get(url+'/api/public/traces/'+tid,auth=auth,timeout=10)
        response.raise_for_status();data=response.json()
        records.append(dict(file=str(path.relative_to(ROOT)),trace_id=tid,
            scores=verify_scores(saved['score_export']['scores'],scores),
            observations=[dict(id=o['id'],name=o['name'],input=o.get('input'),output=o.get('output')) for o in data.get('observations',[])]))
    write(base/'trace-readback.json',records)
    print(json.dumps(dict(traces=len(records),verified=sum(r['scores']['verified'] for r in records),
        scores=sum(len(r['scores']['scores']) for r in records),observations=sum(len(r['observations']) for r in records))))
