"""Offline structure-only rebuild from an immutable 112-document manifest.

No database, PDF extraction, OCR, indexing or model invocation. Each source and its
physical/DocumentIR content is hashed. Existing output directories are refused.
Example: python scripts/rebuild_table_structure.py --manifest work/reload-1123/manifest.json --out work/table-structure-1124/step1
"""
import argparse
import gc
import hashlib
import json
from pathlib import Path

from doc_understanding.domain.pipeline_contracts import CanonicalDocument
from doc_understanding.parser.structure.rebuild import rebuild


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def digest_model(model):
    return hashlib.sha256(model.model_dump_json().encode()).hexdigest()


def physical_digest(doc):
    digest=hashlib.sha256()
    for table in doc.physical_tables:digest.update(bytes.fromhex(digest_model(table)))
    return digest.hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--only',help='optional document SHA prefix for targeted diagnosis')
    args=parser.parse_args()
    entries=json.loads(args.manifest.read_bytes())
    if args.only:entries=[e for e in entries if e['document_id'].removeprefix('sha256:').startswith(args.only)]
    if not entries:raise SystemExit('NO_MATCHING_DOCUMENTS')
    args.out.mkdir(parents=True,exist_ok=False)
    built=[]
    for n,entry in enumerate(entries,1):
        path=Path(entry['document_path']);before_hash=sha(path)
        expected=entry.get('new_document_sha256')
        if expected and expected!=before_hash:raise ValueError('SOURCE_FILE_HASH_MISMATCH')
        doc=CanonicalDocument.model_validate_json(path.read_bytes())
        before_ir=digest_model(doc.document_ir);before_physical=physical_digest(doc)
        updated=rebuild(doc)
        assert digest_model(updated.document_ir)==before_ir and physical_digest(updated)==before_physical
        folder=args.out/'docs'/doc.document_id.removeprefix('sha256:');folder.mkdir(parents=True)
        target=folder/'document.json';target.write_text(updated.model_dump_json(indent=2),encoding='utf8')
        assert sha(path)==before_hash
        record=dict(entry,document_path=str(target.resolve()),previous_document_path=str(path.resolve()),
            previous_document_sha256=before_hash,new_document_sha256=sha(target),document_ir_sha256=before_ir,
            physical_tables_sha256=before_physical,
            inherited_headers=sum(l.status=='accepted' for l in updated.logical_table_links),
            candidate_headers=sum(l.status=='candidate' for l in updated.logical_table_links))
        built.append(record)
        (args.out/'manifest.json').write_text(json.dumps(built,ensure_ascii=False,indent=2),encoding='utf8')
        print(n,len(entries),doc.document_id[7:19],record['inherited_headers'],record['candidate_headers'],flush=True)
        del updated,doc
        gc.collect()
    print('BUILT',len(built),args.out/'manifest.json')


if __name__=='__main__':main()
