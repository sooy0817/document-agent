"""Local immutable experiment snapshots and separate human review events."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2).encode('utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class RunRecord(StrictModel):
    schema_version: Literal['evaluation-run/1.0'] = 'evaluation-run/1.0'
    run_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    recorded_at: str
    title: str
    dataset_role: str
    result_kind: str
    artifacts: dict[str, str]
    metadata: dict = Field(default_factory=dict)


class ReviewDecision(StrictModel):
    case_id: str
    decision: Literal['agree','correction','uncertain']
    note: str = ''


class ReviewBatch(StrictModel):
    schema_version: Literal['evaluation-review/1.0'] = 'evaluation-review/1.0'
    queue_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    reviewer: str = Field(min_length=1)
    reviews: list[ReviewDecision] = Field(min_length=1)


def put(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    try:
        with path.open('xb') as f:
            f.write(data)
    except FileExistsError:
        if path.read_bytes()!=data:
            raise ValueError('Immutable artifact conflict')


def register(ledger, workspace, spec):
    ledger,workspace=Path(ledger),Path(workspace).resolve()
    artifacts={}
    for rel in spec['artifacts']:
        p=(workspace/rel).resolve()
        p.relative_to(workspace)
        data=p.read_bytes()
        h=digest(data)
        put(ledger/'objects'/h,data)
        artifacts[str(p.relative_to(workspace)).replace('\\','/')]=h
    identity={k:spec[k] for k in ['title','dataset_role','result_kind','metadata']}
    identity['artifacts']=artifacts
    rid=digest(encoded(identity))
    path=ledger/'runs'/f'{rid}.json'
    if path.exists():
        old=RunRecord.model_validate_json(path.read_bytes())
        return old
    record=RunRecord(run_id=rid,recorded_at=datetime.now(timezone.utc).isoformat(),**identity)
    put(path,encoded(record.model_dump()))
    return record


def import_reviews(ledger, file):
    ledger=Path(ledger)
    batch=ReviewBatch.model_validate_json(Path(file).read_bytes())
    if not batch.reviewer.strip():
        raise ValueError('Reviewer is required')
    queue=ledger/'queues'/f'{batch.queue_sha256}.json'
    data=queue.read_bytes()
    if digest(data)!=batch.queue_sha256:
        raise ValueError('Queue hash mismatch')
    ids={c['case_id'] for c in json.loads(data)['cases']}
    seen=set()
    for r in batch.reviews:
        if r.case_id not in ids or r.case_id in seen:
            raise ValueError('Unknown or duplicated review case')
        if r.decision=='correction' and not r.note.strip():
            raise ValueError('Correction requires a note')
        seen.add(r.case_id)
    body=batch.model_dump()
    event_id=digest(encoded(body))
    path=ledger/'reviews'/f'{event_id}.json'
    if not path.exists():
        put(path,encoded({'event_id':event_id,'imported_at':datetime.now(timezone.utc).isoformat(),'gold_modified':False,'review':body}))
    return path


def verify(ledger):
    ledger=Path(ledger)
    count=0
    for p in (ledger/'runs').glob('*.json'):
        r=RunRecord.model_validate_json(p.read_bytes())
        body=r.model_dump(exclude={'schema_version','run_id','recorded_at'})
        if digest(encoded(body))!=r.run_id or p.stem!=r.run_id:
            raise ValueError('Run identity mismatch')
        for h in r.artifacts.values():
            if digest((ledger/'objects'/h).read_bytes())!=h:
                raise ValueError('Artifact hash mismatch')
        count+=1
    return count


def main():
    parser=argparse.ArgumentParser(description='Local evaluation history; never publishes facts')
    parser.add_argument('command',choices=['register','import-review','verify'])
    parser.add_argument('--ledger',type=Path,default=Path('data/evaluation-ledger'))
    parser.add_argument('--workspace',type=Path,default=Path('.'))
    parser.add_argument('--file',type=Path)
    args=parser.parse_args()
    if args.command=='verify':
        print(verify(args.ledger))
    elif args.command=='import-review':
        print(import_reviews(args.ledger,args.file))
    else:
        print(register(args.ledger,args.workspace,json.loads(args.file.read_text(encoding='utf-8'))).run_id)


if __name__=='__main__':
    main()
