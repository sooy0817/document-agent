"""Explicit local rule-only experiment; independent outputs, no API/DB writes."""
import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter

from doc_understanding.domain.models import DocumentIR
from doc_understanding.domain.pipeline_contracts import CanonicalDocument
from doc_understanding.parser.assembly.document import assemble, validate_document
from doc_understanding.parser.physical.runner import debug_overlay
from doc_understanding.parser.extraction.physical_evidence_adapter import raster_evidence
from doc_understanding.parser.physical.line_detector import LineDetector
from doc_understanding.parser.physical.physical_models import GridPolicy, PhysicalPageResult
from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer
from doc_understanding.experiments.physical_recovery.recovery import RecoveryAnalyzer, VARIANTS


def read(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--manifest',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--variants',default='0,1,2,3,12,13,23,123')
    args=ap.parse_args()
    if args.output.exists():
        raise ValueError('IMMUTABLE_OUTPUT_ALREADY_EXISTS')
    manifest=read(args.manifest)
    results=[]
    for item in manifest['documents']:
        ir=DocumentIR.model_validate_json(Path(item['ir']).read_bytes())
        run=Path(item['ir']).parent
        source=(run/ir.source.path).read_bytes()
        if hashlib.sha256(source).hexdigest()!=ir.source_sha256:
            raise ValueError('SOURCE_HASH_MISMATCH')
        old=CanonicalDocument.model_validate_json(Path(item['canonical']).read_bytes()) if item.get('canonical') else None
        pages=item['pages']
        cached={}
        for number in pages:
            page=ir.pages[number-1]
            baseline=PhysicalPageResult.model_validate_json((run/f'physical/page-{number:03}.json').read_bytes())
            cached[number]=(page,baseline,None)
        for strategy in VARIANTS:
            if strategy.name not in args.variants.split(','):
                continue
            output=args.output/item['id']/('S'+strategy.name)
            start=perf_counter()
            pt=[]
            st=[]
            stats=[]
            for number in pages:
                page,baseline,pixels=cached[number]
                def raster_loader():
                    if cached[number][2] is None:
                        detected=LineDetector(GridPolicy()).raster(page,raster_evidence(page,run))
                        cached[number]=(page,baseline,detected)
                    return cached[number][2]
                result,log=RecoveryAnalyzer().analyze(ir.document_id,page,strategy,baseline,raster_loader)
                structured=[SemanticTableAnalyzer().analyze(t) for t in result.tables]
                write(output/f'page-{number:03}-physical.json',result.model_dump(mode='json'))
                write(output/f'page-{number:03}-structured.json',[s.model_dump(mode='json') for s in structured])
                (output/f'page-{number:03}-overlay.png').write_bytes(debug_overlay(source,number-1,result))
                pt.extend(result.tables)
                st.extend(structured)
                stats.append(dict(page=number,log=log,tables=len(result.tables),
                    cells=sum(len(t.cells) for t in result.tables),
                    errors=[x.code for t in result.tables for x in t.validation_issues if x.severity=='error']))
            if old:
                # Reuse unselected pages exactly; source IR remains the original whole document.
                pt.extend(t for t in old.physical_tables if t.page_number not in pages)
                st.extend(t for t in old.table_structure if t.page_number not in pages)
            try:
                canonical=assemble(ir,pt,st,source_uri=old.source_uri if old else item.get('url'))
                checks=validate_document(canonical)
                write(output/'canonical.json',canonical.model_dump(mode='json'))
            except ValueError as exc:
                checks={'error':str(exc),'semantics_validated':False}
            entry=dict(id=item['id'],split=item['split'],strategy=strategy.name,pages=stats,
                seconds=perf_counter()-start,canonical_checks=checks,
                input_ir=item['ir'],input_ir_sha256=hashlib.sha256(Path(item['ir']).read_bytes()).hexdigest(),
                source_sha256=ir.source_sha256,output=str(output.resolve()))
            write(output/'result.json',entry)
            results.append(entry)
            print(json.dumps(dict(id=item['id'],strategy=strategy.name,cells=sum(x['cells'] for x in stats),seconds=round(entry['seconds'],2))),flush=True)
    write(args.output/'results.json',dict(manifest=manifest,results=results,external_api_calls=0))


if __name__=='__main__':
    main()
