"""Evaluation orchestration only; frozen production code is reused unchanged."""
import json,hashlib,sys
from pathlib import Path
import pymupdf
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
DATA=ROOT/'data/axis-judge-b-holdout-20261029'
OUT=ROOT/'reports/axis-judge-b-20261029/holdout'
SELECTION={'H01':[3],'H02':[1,2],'H03':[2,3],'H04':[3],'H05':[1,5]}
if __name__=='__main__':
 if '--render' in sys.argv:
  canvas=pymupdf.open();page=canvas.new_page(width=1600,height=1500)
  for i,(id,p) in enumerate((id,p) for id,ps in SELECTION.items() for p in ps):
   d=pymupdf.open(DATA/(id+'.pdf'));x=(i%4)*400;y=(i//4)*750
   page.show_pdf_page(pymupdf.Rect(x,y+25,x+390,y+735),d,p-1);page.insert_text((x+5,y+15),id+' p'+str(p))
  page.get_pixmap().save(DATA/'selection.png')
 else:
  from doc_understanding.parser.pipeline import prepare
  from run_axis_resolution import run_analysis
  OUT.mkdir(parents=True,exist_ok=False)
  (OUT/'selection-before-extraction.json').write_text(json.dumps(SELECTION,indent=2),encoding='utf-8')
  rows=[]
  for id,ps in SELECTION.items():
   run,m=prepare(DATA/(id+'.pdf'),OUT/(id+'-extraction'))
   row=dict(id=id,run=str(run.relative_to(ROOT)),pages=[])
   for p in ps:
    dest=OUT/f'{id}-p{p}';r=run_analysis(run,p,dest)
    row['pages'].append(dict(page=p,source=str(dest.relative_to(ROOT)),regions=len(r['regions'])))
   rows.append(row);print(json.dumps(row),flush=True)
  (OUT/'extraction.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
