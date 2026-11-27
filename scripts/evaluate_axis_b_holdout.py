"""Preselected source targets and immutable candidate-generation orchestration."""
import json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from run_axis_generation import run
BASE=ROOT/'reports/axis-judge-b-20261029';H=BASE/'holdout'
# Evaluation-only references. Never supplied to generation or Judge prompts.
PLAN=[
 ('H01-p3',0,9,['압류'],['설명'],'explicit_headers','new'),
 ('H02-p2',0,3,['1개월미만'],['이율'],'nested_rate','regression'),
 ('H03-p2',2,3,['가입자격'],['내용'],'explicit_headers','new'),
 ('H03-p3',0,1,None,None,'continuation_missing_previous_label','new'),
 ('H04-p3',1,1,['1순위'],[],'headerless_column','new'),
 ('H04-p3',4,5,['거주','대학생'],['3점'],'merged_ancestor','new'),
 ('H05-p1',0,1,['가입대상'],[],'headerless_column','new'),
 ('H05-p1',0,8,['기본금리(연%)','1개월이상3개월미만'],[],'merged_ancestor_headerless','new')]
def save(p,v):
 with p.open('x',encoding='utf-8') as f:json.dump(v,f,ensure_ascii=False,indent=2)
if __name__=='__main__':
 if '--select' in sys.argv:
  rows=[]
  for i,(folder,idx,cell,row,col,kind,split) in enumerate(PLAN):
   source=H/folder/f'region-{idx:03}-source.json';r=json.loads(source.read_text(encoding='utf-8'));target=r['cells'][cell]
   rows.append(dict(id=f'new-{i+1:03}',label=folder+' '+kind,source=str(source.relative_to(ROOT)),
    image=str(source.with_name(f'region-{idx:03}.png').relative_to(ROOT)),target_id=target['id'],value_raw=target['text'],
    expected_row=row,expected_col=col,type=kind,split=split,region_index=idx,folder=folder,
    reference_status='source_visual_review_by_assistant_not_independent_human_gold'))
  save(BASE/'holdout-selection.json',dict(cases=rows,unavailable=[dict(id='new-009',document='H05',page=5,stage='axis_region',reason='ValueError: min() iterable argument is empty',selected=True)],reference_is_never_model_input=True))
 else:
  for line in (ROOT/'.env').read_text(encoding='utf-8-sig').splitlines():
   if line.startswith('OPENAI_API_KEY='):os.environ.setdefault('OPENAI_API_KEY',line.split('=',1)[1].strip().strip('\"\''))
  selection=json.loads((BASE/'holdout-selection.json').read_text(encoding='utf-8'));done={};cases=[];failures=list(selection['unavailable'])
  for c in selection['cases']:
   key=(c['folder'],c['region_index']);out=H/(c['folder']+f'-r{c["region_index"]}-generation')
   if key not in done:
    result=run(H/c['folder'],out,allow_external=True,vision=True,max_calls=1,region_index=c['region_index'],prompt_version='explicit-v2');done[key]=result
    print(json.dumps(dict(folder=c['folder'],region=c['region_index'],calls=result['actual_api_attempts'],cost=result['estimated_cost_usd'])),flush=True)
   path=out/f'region-{c["region_index"]:03}-graph.json';state=json.loads(path.read_text(encoding='utf-8'))
   g=state.get('generated') or {};link=next((x for x in g.get('links',[]) if x['cell_id']==c['target_id']),None)
   if link:cases.append(dict(c,generated=str(path.relative_to(ROOT))))
   else:failures.append(dict(c,stage='candidate_generation',reason='NO_TARGET_LINK',generated=str(path.relative_to(ROOT))))
  save(BASE/'holdout-cases.json',dict(cases=cases,unavailable=failures,reference_is_never_model_input=True))
