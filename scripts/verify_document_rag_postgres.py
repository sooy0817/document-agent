"""Explicit local DB integration acceptance, including real SQL rollback/recovery."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]

from doc_understanding.rag import repository
from doc_understanding.parser.assembly.document import validate_document
from doc_understanding.rag.index import cells_for_table
from doc_understanding.storage.staging.local import ingest, literal, query
from doc_understanding.parser.pipeline import load


def verify(manifest_path,document_path,output):
    first=repository.store(manifest_path,document_path)
    again=repository.store(manifest_path,document_path)
    assert again['duplicate'] and again['run_id']==first['run_id']
    doc=repository.fetch_document(first['run_id'])
    probes=query(f"""SELECT p->>'table_id' table_id,c->>'cell_id' cell_id,
        c->'original_text' value_raw,m->'row_path' row_node_ids,m->'column_path' column_node_ids
        FROM staging.artifact a,
          LATERAL jsonb_array_elements(a.payload->'physical_tables') p,
          LATERAL jsonb_array_elements(p->'cells') c,
          LATERAL jsonb_array_elements(a.payload->'table_structure') s,
          LATERAL jsonb_array_elements(s->'cell_mappings') m
        WHERE a.run_id={literal(first['run_id'])} AND a.artifact_id={literal(repository.ARTIFACT_ID)}
          AND s->>'table_id'=p->>'table_id' AND m->>'cell_id'=c->>'cell_id'""")
    probe_index={row['cell_id']:row for row in probes}
    expected_count=0
    for table in doc.physical_tables:
        for value in cells_for_table(doc,table.table_id):
            row=probe_index[value['cell_id']]
            assert row['value_raw']==value['value_raw']
            assert row['row_node_ids']==value['row_node_ids']
            assert row['column_node_ids']==value['column_node_ids']
            expected_count+=1
    assert len(probe_index)==len(probes)==expected_count
    manifest,ir,layout,artifacts=load(manifest_path)
    payload=doc.model_dump(mode='json')
    artifacts.append((repository.ARTIFACT_ID,'CanonicalDocument',Path(document_path),payload,None))
    fault_run=str(uuid4())
    ingest(ir,layout,Path(manifest['run_dir']),Path(manifest['source']),
           'rag-index-fault:'+fault_run,fault_run,artifacts)
    digest=hashlib.sha256(Path(document_path).read_bytes()).hexdigest()
    try:
        repository.create_index(fault_run,doc,digest,fail=True)
    except RuntimeError:
        pass
    else:
        raise AssertionError('INJECTION_NOT_TRIGGERED')
    snapshot_count=query(f'SELECT count(*) n FROM rag.document_snapshot WHERE run_id={literal(fault_run)}')[0]['n']
    entry_count=query(f'SELECT count(*) n FROM rag.search_entry WHERE run_id={literal(fault_run)}')[0]['n']
    assert snapshot_count==entry_count==0
    repository.create_index(fault_run,doc,digest)
    assert repository.fetch_document(fault_run)==doc
    result=dict(primary=first,duplicate=again,checks=validate_document(doc),
                sql_value_path_rows_verified=len(probes),sql_examples=probes[:4],
                index_failure_injection=dict(run_id=fault_run,snapshots_after_rollback=snapshot_count,
                                             entries_after_rollback=entry_count,recovery_exact=True),
                facts_published=0,semantic_accuracy=None)
    target=Path(output)
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('x',encoding='utf8') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps(dict(sql_value_path_rows_verified=len(probes),idempotent=True,
                          rollback_exact=True,recovery_exact=True)))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--manifest',required=True)
    p.add_argument('--document',required=True)
    p.add_argument('--output',required=True)
    a=p.parse_args()
    verify(a.manifest,a.document,a.output)
