"""Real-source continuation coverage diagnostic; no model calls or diagram grading."""
from run_period_expansion import ROOT,read,write,inventory,render_packet
from doc_understanding.agents.axis_review.axis_resolver import AxisRegion
from doc_understanding.parser.context.period_expansion import expand_context

if __name__=='__main__':
    base=ROOT/'reports/period-expansion-20261103/continuation-01'
    base.mkdir(parents=True,exist_ok=False)
    c=next(c for c in read(ROOT/'reports/period-coverage-20261030/manifest.json')['cases'] if c['id']=='S01')
    docpath=ROOT/c['document_ir'];doc,regions,_=inventory(docpath);rows=[]
    for n,case in enumerate(read(ROOT/'reports/period-coverage-20261030/continuation-checks.json')):
        region=AxisRegion.model_validate(read(ROOT/case['source']))
        packet=expand_context(region,[case['target_cell_id']],doc,regions)
        write(base/f'case-{n:03d}.json',packet)
        image,panels=render_packet(packet,docpath.parent/'source.pdf')
        (base/f'case-{n:03d}.png').write_bytes(image)
        rows.append(dict(page=case['page'],target_cell_id=case['target_cell_id'],
            added=len(packet['additions']),pages=sorted(set(packet['cell_pages'].values())),
            before=packet['before']['targets'][0]['status'],after=packet['after']['targets'][0]['status'],
            missing_before=len(packet['before']['targets'][0]['missing_span_ids']),
            missing_after=len(packet['after']['targets'][0]['missing_span_ids']),panels=panels,
            mode='source coverage diagnostic only; page 2 has diagrams, excluded from semantic/model evaluation',
            actual_api_calls=0,semantic_verified=False))
    write(base/'summary.json',rows)
    print(rows)
