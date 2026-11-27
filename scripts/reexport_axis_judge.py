"""Re-export saved Judge scores without inference or extra traces; immutable recovery output."""
import argparse
import json
from pathlib import Path
import time
from run_axis_judge import read,write,hashes,report
from doc_understanding.runtime.telemetry import Trace,load_config
from doc_understanding.rag.observability.judge import publish_scores,read_scores,verify_scores
from doc_understanding.rag.observability.trace_reader import read_observations


def run(source,output,langfuse_config):
    paths=sorted(p for p in source.iterdir() if p.is_file());before=hashes(paths)
    records=[read(p) for p in sorted(source.glob('case-*.json'))]
    output.mkdir(parents=True,exist_ok=False)
    config=load_config(langfuse_config);trace=Trace(config)
    checks=[]
    for record in records:
        specs=[dict(name=s['name'],value=s['value'],data_type=s['dataType'],comment=s['comment'])
               for s in record['score_export']['scores']]
        exported=publish_scores(trace,config,specs,record['trace_id'],record['candidate_observation_id'])
        check=dict(case_id=record['case_id'],verified=False)
        if trace.client:
            for attempt in range(3):
                try:
                    scores=read_scores(config,record['trace_id'])
                    obs=read_observations(trace.client,config,record['trace_id'])
                    verified=verify_scores(exported['scores'],scores)
                    names={o.get('name') for o in obs['data']}
                    expected={'axis-judge.case','source_candidate','deterministic_check','judge_structure','validate_judgement','record_evaluation'}
                    check.update(verified=verified['verified'] and expected<=names,
                        score_verification=verified,observations=obs['data'],scores=scores)
                    if check['verified']:break
                except Exception as exc:check['error']=type(exc).__name__
                if attempt<2:time.sleep(2)
        record['original_score_export']=record['score_export'];record['score_export']=exported
        record['export_replay_only']=True
        write(output/(record['case_id']+'.json'),record);checks.append(check)
        print(json.dumps(dict(case=record['case_id'],verified=check['verified'],export=exported['status'])),flush=True)
    summary={**read(source/'summary.json'),'langfuse_verified_cases':sum(c['verified'] for c in checks),
        'export_recovery_only':True,'additional_model_calls':0,'additional_model_cost_usd':0,
        'source_run':str(source),'source_run_unchanged':before==hashes(paths),
        'transport_fix':'SDK5 batch vs local v3 object payload; same trace and score IDs'}
    write(output/'summary.json',summary);write(output/'langfuse-readback.json',checks)
    write(output/'source-hashes.json',before);report(output,summary,records)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--langfuse-config',type=Path,required=True)
    run(**vars(p.parse_args()))
