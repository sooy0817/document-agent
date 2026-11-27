"""Rebuild only disposable indexes from stored JSON; never rewrite artifacts."""
import hashlib
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]

from doc_understanding.rag.repository import create_index, fetch_document
from doc_understanding.storage.staging.local import query


def refresh(output):
    rows=query('SELECT run_id,snapshot_sha256 FROM rag.document_snapshot ORDER BY created_at')
    records=[]
    for row in rows:
        doc=fetch_document(row['run_id'])
        before=hashlib.sha256(doc.model_dump_json().encode()).hexdigest()
        create_index(row['run_id'],doc,row['snapshot_sha256'])
        after=hashlib.sha256(fetch_document(row['run_id']).model_dump_json().encode()).hexdigest()
        assert before==after
        records.append(dict(run_id=row['run_id'],canonical_unchanged=True))
    path=Path(output)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf8') as stream:
        json.dump(dict(indexed_snapshots=len(rows),records=records),stream,indent=2)
    print(json.dumps(dict(indexed_snapshots=len(rows),canonical_unchanged=True)))


if __name__=='__main__':
    refresh(sys.argv[1])
