# 역할: 문서별 별도 프로세스로 추출·조립·선택적 적재를 실행하고 해시 검증 후 재개한다.
"""Serial, resumable batch with per-document process timeout and immutable events."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from doc_understanding.parser.assembly.document import build_from_run, sha, validate_document


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def code_fingerprint():
    paths = sorted(Path(__file__).parents[1].rglob('*.py')) + [Path(__file__).with_name('schema.sql')]
    return hashlib.sha256(''.join(p.name + sha(p) for p in paths).encode()).hexdigest()


def inventory(root, output, limit=500, core=25, *, pattern='**/*.pdf', exclude_paths=(), exclude_sha256=()):
    root = Path(root).resolve(strict=True)
    if limit < 1 or core < 0 or core > limit:
        raise ValueError('INVALID_INVENTORY_LIMITS')
    # Paths identify caller-selected exclusions before reading bytes. Hash exclusions
    # also block duplicate content stored under another name. Neither infers semantics.
    excluded = [(root / p).resolve() for p in exclude_paths]
    candidates = [p for p in root.glob(pattern) if p.is_file() and p.suffix.lower() in ('.pdf', '.html', '.htm')
                  and p.resolve().is_relative_to(root)
                  and not any(p.resolve() == x or p.resolve().is_relative_to(x) for x in excluded)]
    candidates.sort(key=lambda p: hashlib.sha256(p.relative_to(root).as_posix().encode()).hexdigest())
    selected, digests, groups = [], set(), set()
    for p in candidates:
        if len(selected) >= limit:
            break
        institution = p.parent.name
        if len(selected) < core and institution in groups:
            continue
        digest = sha(p)
        if digest in digests or digest in exclude_sha256:
            continue
        digests.add(digest)
        groups.add(institution)
        selected.append(dict(source=str(p.resolve()),sha256=digest,group=institution,
                             split='development_unreviewed', core=len(selected)<core,
                             bytes=p.stat().st_size))
    result = dict(root=str(root),pattern=pattern,exclude_paths=list(map(str,exclude_paths)),
                  exclude_sha256=sorted(exclude_sha256),available_candidates=len(candidates),
                  selected=len(selected),core=min(core,len(selected)),
                  policy='unique source SHA; one parent-directory group per core sample; explicit exclusions; no semantic gold',
                  documents=selected)
    write(output,result)
    return result


def worker(source, output, with_db=False):
    start = perf_counter()
    source, output = Path(source), Path(output)
    output.mkdir(parents=True,exist_ok=False)
    if source.suffix.lower() in ('.html','.htm'):
        from doc_understanding.parser.extraction.html_adapter import prepare_html
        run, manifest = prepare_html(source, output/'extraction')
    else:
        from doc_understanding.parser.pipeline import prepare
        run, manifest = prepare(source, output/'extraction')
    origin = source.with_suffix(source.suffix+'.origin.json')
    if origin.exists() and not (run/'source-origin.json').exists():
        (run/'source-origin.json').write_bytes(origin.read_bytes())
    doc = build_from_run(run)
    document_path = output/'document.json'
    write(document_path,doc.model_dump(mode='json'))
    database = None
    if with_db:
        from doc_understanding.rag.repository import store
        database = store(run/'staging-manifest.json', document_path)
    from doc_understanding.rag.index import cells_for_table, make_entries
    values=[v for t in doc.table_structure for v in cells_for_table(doc,t.table_id)]
    result = dict(source=str(source.resolve()),source_sha256=sha(source),
                  document_path=str(document_path.resolve()),document_sha256=sha(document_path),
                  manifest=str((run/'staging-manifest.json').resolve()),
                  database=database,pages=len(doc.document_ir.pages),tables=len(doc.table_structure),
                  contents=len(doc.contents),search_entries=len(make_entries(doc)),
                  extraction_seconds=manifest['seconds'],seconds=perf_counter()-start,
                  disk_bytes=sum(p.stat().st_size for p in output.rglob('*') if p.is_file()),
                  checks=validate_document(doc),
                  processing_status=manifest['status'],
                  page_text_status=[p.text_status for p in doc.document_ir.pages],
                  unresolved_value_paths=sum(not m.row_path or not m.column_path for t in doc.table_structure for m in t.cell_mappings),
                  value_cells=len(values),eligible_value_cells=sum(v['lookup_eligible'] for v in values),
                  tables_without_value_mappings=sum(not t.cell_mappings for t in doc.table_structure),
                  semantic_status='unvalidated_candidate',model_calls=0,cost_usd=0)
    write(output/'result.json',result)
    return result


def batch(manifest_path, output, limit=25, with_db=False, timeout=180):
    manifest = json.loads(Path(manifest_path).read_text(encoding='utf8'))
    output = Path(output)
    output.mkdir(parents=True,exist_ok=True)
    events_file = output/'events.jsonl'
    prior = [json.loads(line) for line in events_file.read_text(encoding='utf8').splitlines()] if events_file.exists() else []
    fingerprint = code_fingerprint()
    seen = {e['source_sha256']: e for e in prior if e['state']=='CANDIDATE_STORED'
            and e.get('code_fingerprint')==fingerprint and e.get('with_db')==with_db}
    def event(record, state, **extra):
        entry = dict(source=record['source'],source_sha256=record['sha256'],state=state,
                     at=datetime.now(timezone.utc).isoformat(),code_fingerprint=fingerprint,
                     with_db=with_db,**extra)
        with events_file.open('a',encoding='utf8') as stream:
            stream.write(json.dumps(entry,ensure_ascii=False)+'\n')
            stream.flush()
            os.fsync(stream.fileno())
    outcomes, started = [], perf_counter()
    for record in manifest['documents'][:max(1,min(limit,500))]:
        p = Path(record['source'])
        if not p.exists() or sha(p)!=record['sha256']:
            event(record,'FAILED',error='SOURCE_MISSING_OR_CHANGED')
            outcomes.append(dict(state='FAILED',source=record['source']))
            continue
        saved = seen.get(record['sha256'])
        if saved:
            path = Path(saved['result_path'])
            try:
                result = json.loads(path.read_text(encoding='utf8'))
                valid = sha(result['document_path']) == result['document_sha256']
                if valid and with_db:
                    from doc_understanding.rag.repository import fetch_document
                    from doc_understanding.domain.pipeline_contracts import CanonicalDocument
                    valid = fetch_document(result['database']['run_id']) == CanonicalDocument.model_validate_json(Path(result['document_path']).read_bytes())
                if valid:
                    event(record,'SKIPPED_VERIFIED',result_path=str(path))
                    outcomes.append(dict(state='SKIPPED_VERIFIED',result=result))
                    continue
            except (OSError, ValueError, RuntimeError):
                pass
        attempt = output/record['sha256'][:16]/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
        event(record,'STARTED',attempt=str(attempt.resolve()))
        cmd = [sys.executable,'-m','doc_understanding.rag.batch','worker',
               '--source',str(p),'--output',str(attempt)]
        if with_db:
            cmd.append('--with-db')
        try:
            process = subprocess.run(cmd,capture_output=True,timeout=timeout,
                                     env={**os.environ,'PYTHONIOENCODING':'utf8'})
            if process.returncode:
                event(record,'FAILED',error='WORKER_FAILED',attempt=str(attempt.resolve()))
                # Raw input text/contact details never enter the shared progress log.
                attempt.mkdir(parents=True,exist_ok=True)
                (attempt/'worker-error.txt').write_bytes(process.stderr)
                outcomes.append(dict(state='FAILED',source=record['source']))
                continue
            result = json.loads((attempt/'result.json').read_text(encoding='utf8'))
            event(record,'CANDIDATE_STORED',result_path=str((attempt/'result.json').resolve()),
                  seconds=result['seconds'],processing_status=result['processing_status'])
            outcomes.append(dict(state='CANDIDATE_STORED',result=result))
        except subprocess.TimeoutExpired:
            event(record,'FAILED',error='DOCUMENT_TIMEOUT',attempt=str(attempt.resolve()))
            outcomes.append(dict(state='FAILED',source=record['source']))
    summary = dict(manifest=str(Path(manifest_path).resolve()),seconds=perf_counter()-started,
                   with_db=with_db,code_fingerprint=fingerprint,
                   candidate_stored=sum(x['state']=='CANDIDATE_STORED' for x in outcomes),
                   skipped_verified=sum(x['state']=='SKIPPED_VERIFIED' for x in outcomes),
                   failed=sum(x['state']=='FAILED' for x in outcomes),outcomes=outcomes,
                   semantics_validated=0,facts_published=0,model_calls=0,cost_usd=0)
    write(output/f'summary-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")}.json',summary)
    print(json.dumps({k:v for k,v in summary.items() if k!='outcomes'}))
    return summary


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('inventory')
    p.add_argument('--root',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--limit',type=int,default=500)
    p.add_argument('--core',type=int,default=25)
    p.add_argument('--pattern',default='**/*.pdf')
    p.add_argument('--exclude-path',action='append',default=[])
    p.add_argument('--exclude-sha256',action='append',default=[])
    p=sub.add_parser('worker')
    p.add_argument('--source',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--with-db',action='store_true')
    p=sub.add_parser('run')
    p.add_argument('--manifest',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--limit',type=int,default=25)
    p.add_argument('--timeout',type=int,default=180)
    p.add_argument('--with-db',action='store_true')
    a=parser.parse_args()
    if a.command=='inventory':
        r=inventory(a.root,a.output,a.limit,a.core,pattern=a.pattern,
                    exclude_paths=a.exclude_path,exclude_sha256=a.exclude_sha256)
        print(json.dumps({k:v for k,v in r.items() if k!='documents'}))
    elif a.command=='worker':
        r=worker(a.source,a.output,a.with_db)
        print(json.dumps(dict(pages=r['pages'],tables=r['tables'])))
    else:
        batch(a.manifest,a.output,a.limit,a.with_db,a.timeout)


if __name__=='__main__':
    main()
