"""Read-only verification of coverage replay traces; no model invocation."""
import json
import httpx
from run_axis_judge import ROOT,read,write
from doc_understanding.runtime.telemetry import load_config
from doc_understanding.rag.observability.judge import verify_scores

if __name__=='__main__':
    base=ROOT/'reports/period-coverage-20261030';config=load_config(ROOT/'infra/langfuse/client.env.local')
    auth=(config['public_key'],config['secret_key']);url=config['base_url'].rstrip('/');scores=[]
    for page in range(1,101):
        r=httpx.get(url+'/api/public/scores',params=dict(limit=100,page=page),auth=auth,timeout=10)
        r.raise_for_status();body=r.json();scores+=body['data']
        if page>=body.get('meta',{}).get('totalPages',1):break
    rows=[]
    for path in sorted((base/'run-03').glob('region-???.json')):
        d=read(path);tid=d['trace_id']
        r=httpx.get(url+'/api/public/traces/'+tid,auth=auth,timeout=10);r.raise_for_status();t=r.json()
        rows.append(dict(trace_id=tid,case=d['case']['id'],scores=verify_scores(d['score_export']['scores'],scores),
            observations=[dict(id=o['id'],name=o['name'],type=o.get('type'),input=o.get('input'),output=o.get('output')) for o in t.get('observations',[])]))
    write(base/'trace-readback.json',rows)
    print(json.dumps(dict(traces=len(rows),verified=sum(r['scores']['verified'] for r in rows),
        observations=sum(len(r['observations']) for r in rows),scores=sum(len(r['scores']['scores']) for r in rows))))
