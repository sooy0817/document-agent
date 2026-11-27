"""Acquire public source PDFs; do not run the evaluated parser during selection."""
import argparse,datetime,hashlib,json
from pathlib import Path
import httpx,pymupdf
ROOT=Path(__file__).resolve().parents[1]
SOURCES=[
('N01','NH농협 개인형 IRP','https://img.nonghyup.com/file/ebank/product/form/IRP_key%20document.pdf'),
('N02','NH농협 디폴트옵션 안정형','https://img.nonghyup.com/file/retire/product/009202201004_pi.pdf'),
('N03','KB퇴직연금 정기예금','https://file.shinhaninvest.com/filedoc/rk/pub/202509135528_3_KB_S_2509.pdf'),
('N04','부산은행 마이플랜 퇴직연금','https://www.imbank.co.kr/down/bsbank02_20220519.pdf'),
('N05','한화투자증권 디폴트옵션','https://www.hanwhawm.com/main/common/common_file/fileView.cmd?category=1&dir=pension01%2F2025%2F05&file=20250521061746726.pdf&store=20250521061746726.pdf'),
('N06','웰컴저축은행 퇴직연금','https://file.koreainvestment.com/Storage/corporate/elsrate/welcome_savingsbank2_2509.pdf'),
('N07','하나은행 디폴트옵션 상품안내','https://mbp.hanabank.com/cont/pdf/product/d_month.pdf'),
('N08','LH 광명 국민임대','https://apply.lh.or.kr/lhapply/lhFile.do?fileid=62515982'),
('N09','LH 임대 모집공고 후보','https://www.myhome.go.kr/hws/com/fms/cvplFileDownload.do?atchFileId=18dc591027035&fileSn=1'),
('N10','GH 매입임대 자격완화','https://apply-cdn.gh.or.kr/sr/sr7150/selectFileDown.do?atchFileDtlSn=36&atchFileSn=1596415&mode=1&pbancNo=795&v=20260617142130')]

def save(path,data):
    with path.open('x',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,indent=2)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--source-json',type=Path);a=p.parse_args();out=a.output
    if a.source_json:SOURCES=json.loads(a.source_json.read_text(encoding='utf-8'))
    out.mkdir(parents=True,exist_ok=False)
    code={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'src').rglob('*') if p.suffix in ('.py','.html')}
    save(out/'code-freeze-before-acquisition.json',dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(),files=code))
    previous={}
    for folder in ['data','reports','work/document-explorer']:
        for path in (ROOT/folder).rglob('*'):
            if path.is_file() and path.suffix.lower()=='.pdf' and out.resolve() not in path.resolve().parents:
                previous.setdefault(hashlib.sha256(path.read_bytes()).hexdigest(),[]).append(str(path.relative_to(ROOT)))
    save(out/'prior-pdf-inventory.json',previous);rows=[]
    for id,title,url in SOURCES:
        row=dict(id=id,title=title,url=url)
        try:
            r=httpx.get(url,follow_redirects=True,timeout=60,headers={'User-Agent':'Mozilla/5.0'})
            r.raise_for_status();raw=r.content
            if not raw.startswith(b'%PDF'):raise ValueError('NOT_PDF')
            path=out/(id+'.pdf');path.write_bytes(raw);sha=hashlib.sha256(raw).hexdigest()
            with pymupdf.open(path) as doc:
                texts=[p.get_text() for p in doc]
                row.update(status='downloaded',sha256=sha,pages=len(doc),existing_duplicates=previous.get(sha,[]))
                (out/(id+'-source.txt')).write_text('\n'.join(f'\nPAGE {i+1}\n{t}' for i,t in enumerate(texts)),encoding='utf-8')
                # Source overview, not evaluated table detection or parser output.
                for start in range(0,len(doc),12):
                    with pymupdf.open() as sheet:
                        canvas=sheet.new_page(width=1200,height=1700)
                        for j in range(start,min(start+12,len(doc))):
                            k=j-start;x=(k%4)*300;y=(k//4)*560
                            canvas.insert_text((x+5,y+15),f'{id} page {j+1}',fontsize=12)
                            canvas.show_pdf_page(pymupdf.Rect(x+5,y+22,x+295,y+555),doc,j)
                        canvas.get_pixmap().save(out/f'{id}-overview-{start//12}.png')
        except Exception as e:row.update(status='failed',error=type(e).__name__,reason=str(e)[:180])
        rows.append(row);save(out/(id+'-acquisition.json'),row);print(json.dumps(row,ensure_ascii=False),flush=True)
    save(out/'acquisition.json',rows)
