"""Acquire public evaluation sources after implementation freeze; never overwrite."""
import json,hashlib
from pathlib import Path
import httpx,pymupdf
ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'data/axis-judge-b-holdout-20261029'
SOURCES=[
 ('H01','NH올원e예금','https://img.nonghyup.com/file/ebank/product/guide/10001160_pi.pdf'),
 ('H02','NH저축은행 퇴직연금','https://file.koreainvestment.com/Storage/corporate/elsrate/NHsavingsbank_2406_9876.pdf'),
 ('H03','KB Star 정기예금','https://img2.kbstar.com/obj/ocommon/20210427_kbstar.pdf'),
 ('H04','구미송정 행복주택','https://www.myhome.go.kr/hws/com/fms/cvplFileDownload.do?atchFileId=17d7d8e7c1061&fileSn=1'),
 ('H05','카카오뱅크 정기예금','https://og.kakaobank.io/download/5146ec47-4968-40c4-af9f-d963e4c09289')]
if __name__=='__main__':
 DEST.mkdir(parents=True,exist_ok=False);rows=[]
 for id,title,url in SOURCES:
  row=dict(id=id,title=title,url=url)
  try:
   r=httpx.get(url,follow_redirects=True,timeout=60);r.raise_for_status();data=r.content
   assert data.startswith(b'%PDF'), 'NOT_PDF'
   path=DEST/(id+'.pdf');path.write_bytes(data)
   d=pymupdf.open(path);row.update(path=str(path.relative_to(ROOT)),sha256=hashlib.sha256(data).hexdigest(),pages=len(d),status='downloaded')
   (DEST/(id+'-native.txt')).write_text('\n'.join(f'\n=== PAGE {i+1} ===\n'+p.get_text() for i,p in enumerate(d)),encoding='utf-8')
  except Exception as e:row.update(status='failed',error=type(e).__name__)
  rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
 (DEST/'acquisition.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
