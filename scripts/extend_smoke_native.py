"""Extend smoke sample to five; preserve scans, screen native text before inference."""
import os
import json
import random
import hashlib
from pathlib import Path
import pymupdf

root=Path(__file__).resolve().parents[1]
out=root/'data/cross-institution-smoke'
mp=out/'manifest.json'
manifest=json.loads(mp.read_text(encoding='utf-8'))
if len(manifest['documents'])!=3:
    raise SystemExit('Extension already applied or unexpected manifest')
(out/'manifest-initial-three.json').write_bytes(mp.read_bytes())
source=Path(os.environ.get("DISCLOSURE_SOURCE_DIR", "data/disclosure-source"), '연말결산/2025')
paths=sorted(p for p in source.rglob('*.pdf') if '정기' in p.name and not any(x in str(p) for x in ['남평','남원','강구']))
random.Random(20261020).shuffle(paths)
screen=[]
for p in paths:
    if p.parent.name in {x['institution'] for x in manifest['documents']}:
        continue
    with pymupdf.open(p) as doc:
        counts=[len(page.get_text().strip()) for page in doc]
    native=sum(x>0 for x in counts)
    screen.append({'path':str(p),'pages':len(counts),'native_pages':native})
    if native < len(counts)*0.8:
        continue
    h=hashlib.sha256(p.read_bytes()).hexdigest()
    if h in {x['sha256'] for x in manifest['documents']}:
        continue
    manifest['documents'].append({'source_path':str(p),'institution':p.parent.name,'period':'2025','document_type':'disclosure','format_family':'native_disclosure_unreviewed','sha256':h,'leakage_group':p.parent.name+'/2025','selection':'seed20261020 native-text screening only; pipeline remains frozen','duplicate_review':'exact SHA dedup; institution-period exclusive; content duplicates not independently audited'})
    if len(manifest['documents'])==5:
        break
manifest['extension_reason']='Initial 2/3 PDFs scanned; preserve them and add 2 native documents within approved maximum5. Not a population-representative accuracy sample.'
mp.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
(out/'native-screening.json').write_text(json.dumps(screen,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps([x['institution'] for x in manifest['documents']],ensure_ascii=False))
