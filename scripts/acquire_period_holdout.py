"""Immutable evaluation acquisition, not parser or inference logic."""
import datetime
import hashlib
import json
import urllib.request
from pathlib import Path
import pymupdf

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/period-decisions-holdout-20261030'
SOURCES=[('S01','신한 Value-up 외화 회전 정기예금',
 'https://img.shinhan.com/sbank2016/seol/20181031919400000009LC000030.PDF?1747927217115='),
 ('W01','우리은행 ISA 예금 상품설명서',
 'https://spot.wooribank.com/pot/bbs?ARTICLE_ID=20253&ATTACH_ID=36893&BOARD_ID=B00176&cmd=download')]

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=OUT)
    OUT=parser.parse_args().output.resolve()
    OUT.mkdir(exist_ok=False)
    existing={}
    for folder in ['data','reports']:
        for p in (ROOT/folder).rglob('*'):
            if p.is_file() and p.suffix.lower()=='.pdf':
                existing.setdefault(hashlib.sha256(p.read_bytes()).hexdigest(),[]).append(str(p.relative_to(ROOT)))
    rows=[]
    for id,title,url in SOURCES:
        try:
            raw=urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=45).read()
            if not raw.startswith(b'%PDF'):raise ValueError('NOT_PDF')
            sha=hashlib.sha256(raw).hexdigest()
            p=OUT/(id+'.pdf');p.write_bytes(raw)
            d=pymupdf.open(p)
            row=dict(id=id,title=title,url=url,sha256=sha,pages=len(d),existing_duplicates=existing.get(sha,[]))
            for n,page in enumerate(d):
                page.get_pixmap(matrix=pymupdf.Matrix(1.25,1.25),alpha=False).save(OUT/f'{id}-p{n+1}.png')
            (OUT/(id+'-source-text.txt')).write_text('\n'.join(f'PAGE {n+1}\n{p.get_text()}' for n,p in enumerate(d)),encoding='utf-8')
            rows.append(row)
            print(json.dumps(row,ensure_ascii=False),flush=True)
        except Exception as exc:
            rows.append(dict(id=id,url=url,error=type(exc).__name__))
            print(id,type(exc).__name__,flush=True)
    (OUT/'acquisition.json').write_text(json.dumps(dict(acquired_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),documents=rows),ensure_ascii=False,indent=2),encoding='utf-8')
