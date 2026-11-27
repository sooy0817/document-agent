"""Frozen extraction and source-review inventory; references never enter the parser."""
from doc_understanding.services import documents
from doc_understanding.web import app
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'src')]


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    with Path(path).open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)


def frozen(manifest):
    files = read(manifest['code_freeze'])['files']
    return all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest() == h for p,h in files.items())


def inside_review(cell, box):
    b = cell['bbox']
    return box[0] <= (b['x0']+b['x1'])/2 <= box[2] and box[1] <= (b['y0']+b['y1'])/2 <= box[3]


def norm(text):
    return re.sub(r'\s+', '', text or '')


def one(document, output):
    from doc_understanding.parser.pipeline import prepare
    pass
    start = time.perf_counter()
    source = Path(document['source_pdf'])
    run, manifest = prepare(source, output/'extraction')
    jid = uuid.uuid4().hex
    folder = app.DATA/jid
    folder.mkdir()
    shutil.copyfile(source, folder/'input.pdf')
    documents.export_run(run, folder, manifest['status'])
    result = read(folder/'result.json')
    cases = []
    for case in document['cases']:
        cells = [dict(c, table_id=t['id']) for t in result['tables'] if t['page']==case['page']
                 for c in t['cells'] if inside_review(c, case['review_bbox'])]
        anchor = norm(case['example']['value_anchor'])
        matches = [c for c in cells if anchor in norm(c['text'])]
        cases.append(dict(**case, observed_cells=len(cells),
            nonempty_cells=sum(bool(norm(c['text'])) for c in cells),
            both_paths_present=sum(bool(c['row_path'] and c['col_path']) for c in cells),
            unresolved=sum(c['state']=='unresolved' for c in cells),
            table_ids=sorted({c['table_id'] for c in cells}),
            anchor_matches=matches,
            first_observation='no_physical_cells' if not cells else 'anchor_not_preserved_in_cells' if not matches else 'source_review_required',
            accuracy=None, human_verified=False))
    review = dict(name=document['id']+' · '+document['title'], document=document,
                  extraction_run=str(run.resolve()), cases=cases,
                  purpose='new_document_source_review', status='human_review_pending',
                  metrics_notice='경로 채움은 정확도가 아닙니다. 원본 초안은 사람 승인 전이며 전체 셀 Gold는 미작성입니다.',
                  actual_api_calls=0, estimated_cost_usd=0, source_code_unchanged=True)
    write(folder/'review.json', review)
    summary = dict(id=document['id'], status='completed', job_id=jid,
                   review_url=f'http://127.0.0.1:8766/?job={jid}&page={cases[0]["page"]}',
                   viewer_folder=str(folder.resolve()), run=str(run.resolve()),
                   pages=len(result['pages']), physical_tables=len(result['tables']),
                   empty_physical_tables=sum(not t['cells'] for t in result['tables']),
                   physical_cells=sum(len(t['cells']) for t in result['tables']),
                   selected_cases=cases, seconds=round(time.perf_counter()-start,2))
    write(output/'result.json', summary)


def batch(selection, output):
    manifest = read(selection)
    if not frozen(manifest): raise RuntimeError('FROZEN_SOURCE_CHANGED')
    output.mkdir(parents=True, exist_ok=False)
    write(output/'selection-copy.json', manifest)
    records=[]
    for document in manifest['documents']:
        dest=output/document['id']; dest.mkdir()
        write(dest/'input.json',document)
        print(document['id']+' start',flush=True)
        try:
            completed=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),
                    '--one',str(dest/'input.json'),'--output',str(dest)],cwd=ROOT,
                    capture_output=True,timeout=600)
            (dest/'stdout.log').write_bytes(completed.stdout)
            (dest/'stderr.log').write_bytes(completed.stderr)
            if completed.returncode:raise RuntimeError('EXTRACTION_OR_EXPORT_FAILED; see stderr.log')
            record=read(dest/'result.json')
        except Exception as exc:
            record=dict(id=document['id'],status='failed',error=str(exc),
                        selected_cases=[dict(c,first_observation='pipeline_failed',accuracy=None) for c in document['cases']])
            write(dest/'failure.json',record)
        records.append(record)
        print(json.dumps({k:v for k,v in record.items() if k not in ('selected_cases',)},ensure_ascii=False),flush=True)
    write(output/'manifest.json',dict(records=records,source_code_unchanged=frozen(manifest),
        documents_denominator=len(manifest['documents']),cases_denominator=manifest['case_count'],
        actual_api_calls=0,estimated_cost_usd=0,accuracy=None,human_gold_approved=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--selection',type=Path)
    p.add_argument('--one',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.one:one(read(a.one),a.output)
    else:batch(a.selection,a.output)
