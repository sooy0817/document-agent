"""Read back scores and observation summaries; no inference or writes to server."""
import json,sys
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from doc_understanding.runtime.telemetry import load_config
from doc_understanding.rag.observability.judge import verify_scores
BASE=ROOT/'reports/axis-judge-b-20261029'
if __name__=='__main__':
 config=load_config(ROOT/'infra/langfuse/client.env.local');auth=(config['public_key'],config['secret_key']);url=config['base_url'].rstrip('/')
 scores=[]
 for page in range(1,101):
  r=httpx.get(url+'/api/public/scores',params=dict(limit=100,page=page),auth=auth,timeout=10);r.raise_for_status();body=r.json();scores+=body['data']
  if page>=body.get('meta',{}).get('totalPages',1):break
 rows=[]
 for p in sorted(BASE.glob('*-01/*-[AB].json')):
  d=json.loads(p.read_text(encoding='utf-8'));tid=d.get('trace_id')
  row=dict(file=str(p.relative_to(ROOT)),trace_id=tid)
  if tid:
   try:
    r=httpx.get(url+'/api/public/traces/'+tid,auth=auth,timeout=10);r.raise_for_status();t=r.json()
    row.update(scores=verify_scores(d['score_export']['scores'],scores),observations=[dict(id=o['id'],name=o['name'],output=o.get('output')) for o in t.get('observations',[])])
   except Exception as e:row['error']=type(e).__name__
  rows.append(row)
 with (BASE/'trace-readback.json').open('x',encoding='utf-8') as f:json.dump(rows,f,ensure_ascii=False,indent=2)
 print(json.dumps(dict(traces=len(rows),scores_verified=sum(r.get('scores',{}).get('verified',False) for r in rows),observations=sum(len(r.get('observations',[])) for r in rows))))
