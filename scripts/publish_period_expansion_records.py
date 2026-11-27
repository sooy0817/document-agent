"""Recover observability from saved executions; never rerun/bill model calls."""
import argparse
import httpx
from run_period_expansion import ROOT,read,write
from doc_understanding.runtime.telemetry import Trace,load_config
from doc_understanding.rag.observability.judge import publish_scores,verify_scores

BASE=ROOT/'reports/period-expansion-20261103'

def publish():
    config=load_config(ROOT/'infra/langfuse/client.env.local');trace=Trace(config)
    if trace.client is None:raise RuntimeError('LOCAL_TRACE_CLIENT_UNAVAILABLE')
    rows=[];reviews=read(BASE/'evaluation-reviewed.json')['targets']
    for path in sorted((BASE/'live-01').glob('case-???.json')):
        record=read(path);tid=oid=None
        with trace.span('period-expansion-saved-live-record',dict(run_id='period-expansion-20261103',
                mode='saved_execution_reexport_no_new_model_calls',source_cell_ids=record['case']['target_ids'])) as box:
            tid=trace.client.get_current_trace_id();oid=trace.client.get_current_observation_id()
            for name,state in [('original_fresh_execution',record['original'])]+(
                    [('expanded_fresh_execution',record['expansion']['result'])] if 'result' in record['expansion'] else []):
                with trace.span(name,dict(mode='saved_execution_reexport')):
                    for key in ['proposal','inventory']:
                        call=state['raw'][key+'_record'];value=state['raw'][key]
                        with trace.span('saved_'+key,generation=False) as b:
                            b['output']=dict(status=call['status'],model=call['model'],usage=call['usage'],
                                estimated_cost_usd=call['estimated_cost_usd'],mode='original_usage_not_new_billing',
                                validation=[dict(cell_id=d['target_cell_id'],status=d['status'],reason=d['reason'],
                                    source_cell_ids=d['supporting_cell_ids'],items=[dict(source_cell_ids=r['definition_cell_ids'],
                                        row_node_ids=[r['duration_label_cell_id']]) for r in d['relations']]) for d in (value or {}).get('decisions',[])])
                    with trace.span('saved_coverage_and_comparison') as b:
                        b['output']=dict(validation=[dict(cell_id=t['target_cell_id'],status=t['status'],span_refs=t['missing_span_ids'])
                            for t in state['coverage']['targets']],result=dict(validation=state['comparison']['targets']),
                            semantic_status='model_agreement_not_source_correctness',facts_published=0)
            with trace.span('saved_context_collection') as b:
                b['output']=dict(source_cell_ids=[a['cell_id'] for a in record['expansion']['packet']['additions']],
                                retry_count=record['expansion']['retry_count'],status=record['expansion']['status'])
            specs=[]
            selected=[r for r in reviews if r['case']==record['case']['id']]
            for i,r in enumerate(selected):
                for name,value in [('model_state',r['predicted_status']),('reference_draft_state',r['reference_status']),
                                   ('source_review_draft','match' if r['source_review_match'] else 'mismatch')]:
                    specs.append(dict(name=f'expansion_review.t{i}.{name}',value=value,data_type='CATEGORICAL',
                        comment='Assistant source-review draft, not human Gold. target='+r['target_cell_id']+' | '+r['reason']))
            exported=publish_scores(trace,config,specs,tid,oid)
            box['output']=dict(status='saved_execution_reexported',semantic_status='not_verified',facts_published=0)
        trace.flush()
        rows.append(dict(case=record['case']['id'],trace_id=tid,trace_url=trace.client.get_trace_url(trace_id=tid),
                         original_trace_id=record['trace_id'],score_export=exported,actual_api_calls=0))
    write(BASE/'trace-reexport.json',dict(records=rows,actual_api_calls=0,trace_warnings=trace.warnings))
    print([(r['case'],r['trace_id']) for r in rows])

def verify():
    config=load_config(ROOT/'infra/langfuse/client.env.local');url=config['base_url'].rstrip('/')
    auth=(config['public_key'],config['secret_key']);scores=[];rows=[]
    for page in range(1,101):
        r=httpx.get(url+'/api/public/scores',params=dict(limit=100,page=page),auth=auth,timeout=10)
        r.raise_for_status();body=r.json();scores+=body['data']
        if page>=body.get('meta',{}).get('totalPages',1):break
    for r in read(BASE/'trace-reexport.json')['records']:
        response=httpx.get(url+'/api/public/traces/'+r['trace_id'],auth=auth,timeout=10)
        response.raise_for_status();t=response.json()
        rows.append(dict(case=r['case'],trace_id=r['trace_id'],trace_url=r['trace_url'],
            scores=verify_scores(r['score_export']['scores'],scores),
            observations=[dict(id=o['id'],name=o['name'],type=o.get('type'),input=o.get('input'),output=o.get('output')) for o in t.get('observations',[])]))
    write(BASE/'trace-readback.json',rows)
    print(dict(traces=len(rows),verified=sum(r['scores']['verified'] for r in rows),
               observations=sum(len(r['observations']) for r in rows),scores=sum(len(r['scores']['scores']) for r in rows)))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--verify',action='store_true');a=p.parse_args()
    verify() if a.verify else publish()
