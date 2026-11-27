"""Frozen A/B evaluation of identical saved candidates; explicit live calls only."""
import argparse
import json
import os
from pathlib import Path
from typing import TypedDict
from run_axis_judge import read,write,hashes,ROOT
from doc_understanding.agents.axis_review.axis_resolver import AxisRegion
from doc_understanding.agents.axis_review.axis_generation import GeneratedStructure, validate_generated
from doc_understanding.agents.axis_review.axis_judge import JudgeResult, validate_judgement, judgement_summary, score_specs, judge_request
from doc_understanding.agents.axis_review.axis_judge_b import ExplicitJudgeResult, ComparisonJudgeAdapter, validate_explicit
from doc_understanding.runtime.telemetry import Trace,load_config
from doc_understanding.rag.observability.judge import publish_scores


class State(TypedDict,total=False):
    judgement:dict|None
    model_record:dict
    validation:dict
    observation_id:str|None


def run(manifest,output,*,allow_external=False,max_calls=10,langfuse_config=None):
    if not allow_external:raise ValueError('EXPLICIT_LIVE_FLAG_REQUIRED')
    cases=read(manifest)['cases']
    if len(cases)*2>max_calls:raise ValueError('CALL_BUDGET_TOO_SMALL')
    loaded=[];paths=[manifest]
    for c in cases:
        r=AxisRegion.model_validate(read(ROOT/c['source']));g=GeneratedStructure.model_validate(read(ROOT/c['generated'])['generated'])
        judge_request(r,g,c['target_id']);image=(ROOT/c['image']).read_bytes()
        loaded.append((c,r,g,image));paths.extend(ROOT/c[k] for k in ['source','generated','image'])
    output.mkdir(parents=True,exist_ok=False);before=hashes(paths)
    write(output/'freeze.json',dict(inputs=before,code=hashes([
        ROOT/'src/doc_understanding/agents/axis_review/axis_judge.py',
        ROOT/'src/doc_understanding/agents/axis_review/axis_judge_b.py',Path(__file__)])))
    config=load_config(langfuse_config) if langfuse_config else None
    trace=Trace(config);adapter=ComparisonJudgeAdapter(allow_external=True,max_calls=max_calls);rows=[]
    from langgraph.graph import StateGraph,START,END
    for number,(case,r,g,image) in enumerate(loaded):
        # Alternate order to limit systematic order/cache effects; candidate input stays identical.
        for variant in (['A','B'] if number%2==0 else ['B','A']):
            cid=case['id'];target=case['target_id'];cells={c.id:c for c in r.cells}
            link=next(p for p in g.links if p.cell_id==target)
            def prepare(state):
                oid=None
                with trace.span('source_candidate',dict(cell_id=target,value_raw=cells[target].text,
                    row_path=[cells[i].text for i in link.row_path],column_path=[cells[i].text for i in link.column_path],
                    row_node_ids=link.row_path,column_node_ids=link.column_path)) as box:
                    if trace.client:
                        try:oid=trace.client.get_current_observation_id()
                        except Exception:pass
                    box['output']=dict(method=variant,semantic_status='candidate')
                return dict(observation_id=oid)
            def judge(state):
                with trace.span('judge_structure',dict(method=variant,cell_id=target),generation=True) as box:
                    j,record=adapter.judge(r,g,target,image,variant)
                    summary=judgement_summary(j)
                    if isinstance(j,ExplicitJudgeResult):
                        summary['validation'] += [dict(kind=a.axis+'_inventory',status=a.existence,
                            row_node_ids=a.ancestor_header_ids,column_node_ids=a.direct_header_ids,
                            source_cell_ids=a.missing_header_ids,reason=a.reason,
                            evidence_ids=a.evidence_cell_ids) for a in j.axes]
                    box['output']={**summary,'model':record.get('model'),'usage':record.get('usage') or {},
                        'estimated_cost_usd':record.get('estimated_cost_usd') or 0}
                return dict(judgement=j.model_dump(mode='json') if j else None,model_record=record)
            def check(state):
                cls=JudgeResult if variant=='A' else ExplicitJudgeResult
                j=cls.model_validate(state['judgement']) if state.get('judgement') else None
                v=validate_judgement(r,j) if variant=='A' else validate_explicit(r,g,target,j)
                with trace.span('validate_judgement',dict(method=variant)) as box:
                    box['output']=dict(passed=v['valid'],errors=v['errors'],semantic_status='review_required')
                return dict(validation=v)
            graph=StateGraph(State)
            for name,fn in [('prepare_candidate',prepare),('judge_llm',judge),('validate_judgement',check)]:graph.add_node(name,fn)
            graph.add_edge(START,'prepare_candidate');graph.add_edge('prepare_candidate','judge_llm');graph.add_edge('judge_llm','validate_judgement');graph.add_edge('validate_judgement',END)
            tid=None
            with trace.span('axis-judge-ab',dict(run_id=cid,method=variant)) as root:
                if trace.client:
                    try:tid=trace.client.get_current_trace_id()
                    except Exception:pass
                compiled=graph.compile();state=compiled.invoke({},config={'recursion_limit':10})
                if not (output/'graph.mmd').exists():(output/'graph.mmd').write_text(compiled.get_graph().draw_mermaid(),encoding='utf-8')
                cls=JudgeResult if variant=='A' else ExplicitJudgeResult
                j=cls.model_validate(state['judgement']) if state.get('judgement') else None
                specs=score_specs(j) if state['validation']['valid'] else [dict(name='axis_judge.status',value='invalid_or_unavailable',data_type='CATEGORICAL',comment=','.join(state['validation']['errors']))]
                exported=publish_scores(trace,config,specs,tid,state.get('observation_id'))
                root['output']=dict(status=j.verdict if j else 'unavailable',passed=state['validation']['valid'],facts_published=0)
            record=dict(**state,case_id=cid,variant=variant,trace_id=tid,score_export=exported,
                source=case['source'],generated=case['generated'],image=case['image'],target_id=target,
                value_raw=cells[target].text,row_labels=[cells[i].text for i in link.row_path],
                column_labels=[cells[i].text for i in link.column_path],semantic_verified=False,facts_published=0)
            if trace.client and tid:
                try:record['trace_url']=trace.client.get_trace_url(trace_id=tid)
                except Exception:pass
            write(output/f'{cid}-{variant}.json',record);trace.flush();rows.append(record)
            print(json.dumps(dict(case=cid,variant=variant,verdict=j.verdict if j else None,
                valid=state['validation']['valid'],errors=state['validation']['errors'],
                scores={x.dimension:x.score for x in j.grades} if j else {},cost=state['model_record'].get('estimated_cost_usd')),ensure_ascii=False),flush=True)
    summary=dict(cases=len(cases),actual_api_calls=adapter.calls,completed_calls=sum(x['model_record']['status']=='completed' for x in rows),
        estimated_cost_usd=sum(x['model_record'].get('estimated_cost_usd') or 0 for x in rows),
        unknown_cost_calls=sum(x['model_record'].get('estimated_cost_usd') is None for x in rows),
        inputs_unchanged=before==hashes(paths),facts_published=0,semantic_verified=False)
    write(output/'summary.json',summary);print(json.dumps(summary),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--allow-external',action='store_true');p.add_argument('--max-calls',type=int,default=10)
    p.add_argument('--langfuse-config',type=Path);p.add_argument('--env-file',type=Path)
    a=vars(p.parse_args());env=a.pop('env_file')
    if env and a['allow_external']:
        for line in env.read_text(encoding='utf-8-sig').splitlines():
            if line.strip().startswith('OPENAI_API_KEY='):
                os.environ.setdefault('OPENAI_API_KEY',line.split('=',1)[1].strip().strip('\"\''))
    run(**a)
