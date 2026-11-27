"""Read an immutable staging run and write a new axis sidecar, never production DB.

python scripts/run_axis_resolution.py --run RUN --page 2 --output NEW_DIR
Add --compare-models --allow-external for bounded explicit text/vision comparison.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from doc_understanding.domain.models import DocumentIR
from doc_understanding.parser.physical.physical_models import PhysicalTableIR
from doc_understanding.agents.axis_review.axis_resolver import regions_for, rule_proposal, check_proposal, structured_views, geometry_options
from doc_understanding.agents.axis_review.axis_provider import AxisModelAdapter
from doc_understanding.agents.axis_review.axis_validation import audit_region, validated_views, audit_mapping_coverage


def write(path,data):
    with path.open('x',encoding='utf-8') as f:
        json.dump(data,f,ensure_ascii=False,indent=2)


def run_analysis(run, page_number, output, compare_models=False, allow_external=False, max_calls=6):
    if compare_models and not allow_external:
        raise ValueError('EXTERNAL_CALL_NOT_AUTHORIZED')
    output.mkdir(parents=True,exist_ok=False)
    ir_path=run/'document-ir.json'
    ir=DocumentIR.model_validate_json(ir_path.read_bytes())
    page=ir.pages[page_number-1]
    paths=list((run/'physical').glob(f'page-{page_number:03}-table-*.json'))
    physical_paths=[p for p in paths if not p.stem.endswith('-structured')]
    physical=[PhysicalTableIR.model_validate_json(p.read_bytes()) for p in sorted(physical_paths)]
    preserved={str(p.resolve()):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths+[ir_path]}
    baseline=[]
    for path in paths:
        if path.stem.endswith('-structured'):
            baseline.append(json.loads(path.read_text(encoding='utf-8')))
    write(output/'baseline-structured.json',baseline)
    adapter=AxisModelAdapter(allow_external=allow_external,max_calls=max_calls)
    import pymupdf
    pdf=pymupdf.open(run/'source.pdf')
    regions=regions_for(page,physical)
    rows=[]
    for index,region in enumerate(regions):
        name=f'region-{index:03}'
        write(output/(name+'-source.json'),region.model_dump(mode='json'))
        box=pymupdf.Rect(region.bbox.xyxy)+(-4,-4,4,4)
        pix=pdf[page_number-1].get_pixmap(matrix=pymupdf.Matrix(2,2),clip=box,alpha=False)
        image=pix.tobytes('png');(output/(name+'.png')).write_bytes(image)
        proposal=rule_proposal(region)
        modes={'rules':(proposal,None)}
        if compare_models and geometry_options(region):
            # Run models on identical regions irrespective of rule successes for fair ablation.
            for mode,img in [('text',None),('vision',image)]:
                try:
                    modes[mode]=adapter.resolve(region,img)
                except ValueError as exc:
                    from doc_understanding.agents.axis_review.axis_resolver import AxisProposal
                    modes[mode]=(AxisProposal(links=[],abstain_cell_ids=[c.id for c in region.cells]),
                                 dict(status='not_called',reason=str(exc),attempts=0))
        record=dict(id=region.id,source=name+'-source.json',image=name+'.png',cells=len(region.cells),modes={})
        for mode,(proposal,call) in modes.items():
            geometry_checked=check_proposal(region,proposal)
            audit=audit_region(region,proposal)
            checked=audit.checked_links
            result=dict(proposal=proposal.model_dump(mode='json'),
                        checked=[c.model_dump(mode='json') for c in checked],call=call,
                        geometry_checked=[c.model_dump(mode='json') for c in geometry_checked],
                        validation=audit.model_dump(mode='json'),
                        baseline_coverage=[c.model_dump(mode='json') for c in audit_mapping_coverage(region,baseline)],
                        unlinked_cell_ids=[c.id for c in region.cells if c.id not in {p.cell_id for p in proposal.links}],
                        facts_published=0)
            write(output/(name+'-'+mode+'.json'),result)
            views=validated_views(region,audit,[b for b in baseline if b['table_id'] in {c.table_id for c in region.cells}])
            write(output/(name+'-'+mode+'-structured.json'),[v.model_dump(mode='json') for v in views])
            record['modes'][mode]=dict(proposed=len(checked),supported=sum(c.status=='supported_candidate' for c in checked),
                unresolved=sum(c.status=='unresolved' for c in checked),result=name+'-'+mode+'.json')
        rows.append(record)
    pdf.close()
    unchanged=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in preserved.items())
    manifest=dict(schema_version='axis-resolution-run/1.0',source_run=str(run.resolve()),page=page_number,
                  original_hashes=preserved,originals_unchanged=unchanged,regions=rows,
                  model_calls=adapter.calls,facts_published=0,semantic_verified=False)
    write(output/'manifest.json',manifest)
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True)
    p.add_argument('--page',type=int,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--compare-models',action='store_true');p.add_argument('--allow-external',action='store_true')
    p.add_argument('--max-calls',type=int,default=6)
    a=p.parse_args();r=run_analysis(a.run,a.page,a.output,a.compare_models,a.allow_external,a.max_calls)
    print(json.dumps({k:r[k] for k in ['page','originals_unchanged','regions','model_calls']},ensure_ascii=False))
