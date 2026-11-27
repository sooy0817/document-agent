"""Attach separate, pre-existing source-reference checks; never overwrite Judge scores."""
import argparse
import json
from pathlib import Path
import time
from run_axis_judge import read,write
from doc_understanding.runtime.telemetry import Trace,load_config
from doc_understanding.rag.observability.judge import publish_scores,read_scores,verify_scores


def run(source,review,output,langfuse_config):
    if output.exists(): raise FileExistsError(output)
    config=load_config(langfuse_config);trace=Trace(config);records=[]
    for item in read(review)['source_review']:
        case=read(source/(item['case_id']+'.json'));specs=[]
        for axis,key in [('row','row_exact'),('column','column_exact')]:
            label_key='col' if axis=='column' else 'row'
            reason=json.dumps(dict(reference=item['expected_'+label_key],candidate=item['candidate_'+label_key],
                method='existing_source_reference_exact_path_after_whitespace_normalization',
                note='Separate source-reference check, NOT Judge opinion or independent holdout'),ensure_ascii=False)
            specs.append(dict(name='axis_reference.'+axis+'_path_exact',value=float(item[key]),data_type='BOOLEAN',comment=reason))
        exported=publish_scores(trace,config,specs,case['trace_id'],case['candidate_observation_id'])
        verified={'verified':False}
        if trace.client:
            for attempt in range(3):
                try:verified=verify_scores(exported['scores'],read_scores(config,case['trace_id']))
                except Exception as exc:verified={'verified':False,'error':type(exc).__name__}
                if verified['verified']:break
                if attempt<2:time.sleep(2)
        records.append(dict(case_id=item['case_id'],trace_id=case['trace_id'],export=exported,readback=verified))
        print(json.dumps(dict(case=item['case_id'],verified=verified['verified'])),flush=True)
    write(output,dict(actual_model_calls=0,additional_model_cost_usd=0,records=records))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--review',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--langfuse-config',type=Path,required=True)
    run(**vars(p.parse_args()))
