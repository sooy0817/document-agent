"""Explicit local parser experiment entry point; no upload or stable parser promotion."""
from doc_understanding.experiments.parser_repair import queue
from doc_understanding.runtime import review_queue
import argparse
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--review-id',required=True)
    parser.add_argument('--run-one',action='store_true')
    parser.add_argument('--retry',action='store_true')
    parser.add_argument('--extra-attempt',action='store_true')
    parser.add_argument('--mode',choices=['local_refresh','code_patch'],default='local_refresh')
    parser.add_argument('--apply-passing',action='store_true',help='Apply only an exact completed registered patch that passed source and regression checks')
    args=parser.parse_args()
    data=ROOT/'work/document-explorer'
    q=queue.RepairQueue(data,review_queue.ReviewQueue(data))
    ident=q.enqueue(args.review_id)
    if args.retry:q.retry(ident)
    if args.apply_passing:q.apply_passing_result(ident)
    print('parser_experiment='+ident,flush=True)
    if args.run_one:
        q.enable(True,args.mode)
        original=q.progress
        def progress(ident,stage,message):
            original(ident,stage,message);print(stage+': '+message,flush=True)
        q.progress=progress
        q.run_one(explicit_extra_attempt=args.extra_attempt)
    row=next(i for i in q.overview()['items'] if i['id']==ident)
    print('status='+row['status']+' error='+str(row['error']),flush=True)
