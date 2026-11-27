"""Explicit development samples only. Source bytes are local, immutable and hashed."""
import argparse
import hashlib
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def fetch(url, destination):
    path = Path(destination)
    if path.exists():
        raise FileExistsError(path)
    request = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0 (document-research)'})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read(10_000_001)
        content_type = response.headers.get('Content-Type')
    if len(raw)>10_000_000:
        raise ValueError('SOURCE_BYTE_LIMIT')
    if path.suffix == '.pdf' and not raw.startswith(b'%PDF'):
        raise ValueError('NOT_PDF')
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as f:
        f.write(raw)
    record = dict(url=url, source_path=str(path.resolve()), content_type=content_type,
                  sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw),
                  fetched_at=datetime.now(timezone.utc).isoformat(), purpose='development_smoke_not_gold')
    path.with_suffix(path.suffix+'.origin.json').write_text(json.dumps(record,indent=2),encoding='utf8')
    print(json.dumps(record))


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--url',required=True)
    p.add_argument('--output',required=True)
    a=p.parse_args()
    fetch(a.url,a.output)
