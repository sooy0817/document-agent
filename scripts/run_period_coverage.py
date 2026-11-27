"""Replay identical decisions through source-context coverage checks. Zero model calls."""
import argparse
import hashlib
import json
from pathlib import Path
from run_axis_judge import ROOT,read,write,hashes
from doc_understanding.domain.models import DocumentIR
from doc_understanding.agents.axis_review.axis_resolver import AxisRegion, digest
from doc_understanding.parser.context.period_context import period_request
from doc_understanding.parser.context.period_decisions import PeriodDecisions
from doc_understanding.parser.context.period_coverage import build_coverage_graph, VERSION
from doc_understanding.runtime.telemetry import Trace,load_config
from doc_understanding.rag.observability.judge import publish_scores


class ReplayAdapter:
    replay=True
    def __init__(self,saved):self.saved=saved;self.calls=0
    def call(self,region,targets,img,stage):
        key='proposal' if stage=='propose' else 'inventory';record=self.saved[key+'_record']
        _,request=period_request(region,targets)
        if (record['source_sha256']!=digest(region) or record['request']!=json.loads(json.dumps(request))
                or record['image_sha256']!=hashlib.sha256(img).hexdigest()):
            raise ValueError('REPLAY_INPUT_MISMATCH')
        self.calls+=1
        result=PeriodDecisions.model_validate(self.saved[key]) if self.saved[key] else None
        return result,dict(record,status='replayed',usage=None,estimated_cost_usd=0,
            original_usage=record['usage'],original_cost=record['estimated_cost_usd'])


def run(manifest,output,langfuse_config=None):
    cases=read(manifest)['cases'];loaded=[];paths=[manifest]
    for c in cases:
        saved=read(ROOT/c['saved']);source=saved['case']['source'];image=saved['case']['image']
        region=AxisRegion.model_validate(read(ROOT/source));document=DocumentIR.model_validate(read(ROOT/c['document_ir']))
        loaded.append((c,saved,region,document,(ROOT/image).read_bytes()))
        paths.extend([ROOT/c['saved'],ROOT/source,ROOT/image,ROOT/c['document_ir']])
    output.mkdir(parents=True,exist_ok=False)
    paths+=list((ROOT/'reports/period-decisions-20261030').rglob('*.json'))
    paths+=list((ROOT/'reports/period-context-20261030').rglob('*.json'))
    protected=hashes(paths)
    write(output/'freeze.json',dict(inputs=protected,code=hashes([
        ROOT/'src/doc_understanding/parser/context/period_coverage.py',
        ROOT/'src/doc_understanding/parser/context/period_decisions.py',Path(__file__)])))
    config=load_config(langfuse_config) if langfuse_config else None;trace=Trace(config);records=[]
    for n,(case,saved,region,document,img) in enumerate(loaded):
        adapter=ReplayAdapter(saved);targets=saved['case']['target_ids'];tid=oid=None
        graph=build_coverage_graph(region,targets,img,adapter,document,trace)
        if n==0:(output/'graph.mmd').write_text(graph.get_graph().draw_mermaid(),encoding='utf-8')
        with trace.span('period-context-coverage-replay',dict(run_id=output.name,source_cell_ids=targets,mode='replay_no_model_calls')) as box:
            if trace.client:
                try:tid=trace.client.get_current_trace_id();oid=trace.client.get_current_observation_id()
                except Exception:pass
            state=graph.invoke({},config={'recursion_limit':10})
            specs=[]
            for i,(before,after) in enumerate(zip(state['raw']['proposal']['decisions'],state['guarded_proposal']['decisions'])):
                comment='target='+before['target_cell_id']+' | '+str(after.get('missing_evidence') or after['reason'])
                for phase,value in [('before',before['status']),('after',after['status'])]:
                    specs.append(dict(name=f'coverage.target{i}.{phase}',value=value,data_type='CATEGORICAL',comment=comment))
            exported=publish_scores(trace,config,specs,tid,oid)
            box['output']=dict(status='replayed',semantic_status=state['sidecar']['status'],facts_published=0,
                validation=[dict(cell_id=t['target_cell_id'],status=t['status'],span_refs=t['missing_span_ids']) for t in state['coverage']['targets']])
        record=dict(**state,case=case,source=saved['case']['source'],image=saved['case']['image'],trace_id=tid,score_export=exported,
            actual_api_calls=0,estimated_cost_usd=0,raw_proposal_unchanged=state['raw']['proposal']==saved['proposal'],
            raw_inventory_unchanged=state['raw']['inventory']==saved['inventory'])
        if trace.client and tid:
            try:record['trace_url']=trace.client.get_trace_url(trace_id=tid)
            except Exception:pass
        write(output/f'region-{n:03d}.json',record);write(output/f'region-{n:03d}-sidecar.json',state['sidecar'])
        trace.flush();records.append(record)
        print(json.dumps(dict(id=case['id'],coverage=[(t['target_cell_id'].split('/')[-1],t['status'],len(t['missing_span_ids'])) for t in state['coverage']['targets']],
            changes=[(c['stage'],c['target_cell_id'].split('/')[-1],c['before'],c['after']) for c in state['changes']]),ensure_ascii=False),flush=True)
    summary=dict(version=VERSION,regions=len(records),targets=sum(len(r['coverage']['targets']) for r in records),
        generator_downgrades=sum(c['stage']=='proposal' for r in records for c in r['changes']),
        actual_api_calls=0,estimated_cost_usd=0,inputs_unchanged=protected==hashes(paths),
        raw_model_results_unchanged=all(r['raw_proposal_unchanged'] and r['raw_inventory_unchanged'] for r in records),
        trace_warnings=trace.warnings,semantic_verified=False,facts_published=0)
    write(output/'summary.json',summary);print(json.dumps(summary),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--langfuse-config',type=Path)
    run(**vars(p.parse_args()))
