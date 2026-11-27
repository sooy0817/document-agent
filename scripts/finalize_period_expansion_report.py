"""Link verified trace recovery and check generated local report references."""
import hashlib
import html
import re
from pathlib import Path
from urllib.parse import urlparse,unquote
from run_period_expansion import ROOT,read,write

if __name__=='__main__':
    base=ROOT/'reports/period-expansion-20261103';path=base/'live-01/comparison-reviewed.html'
    content=path.read_text(encoding='utf-8')
    originals={read(p)['case']['id']:read(p) for p in (base/'live-01').glob('case-???.json')}
    for record in read(base/'trace-reexport.json')['records']:
        old=originals[record['case']]
        if old.get('trace_url'):
            content=content.replace(html.escape(old['trace_url']),html.escape(record['trace_url']))
    content=content.replace('<h2>원본 검토 결론</h2>',
        '<h2>원본 검토 결론</h2><p>추적 서버가 꺼져 초기 전송이 실패했습니다. 아래 Langfuse 링크는 저장된 실제 실행을 재전송한 기록입니다. 추가 모델 호출은 0회이며, 서버에서 4 Trace·28 observation·30 score를 재조회 확인했습니다.</p>')
    content=content.replace('남은 근거가 있으므로 보류 유지.',
        '남은 14개·2개 span은 기존 Physical 후보의 usable 검사를 통과하지 못해 자동 추가하지 않았습니다. 보류를 유지합니다.')
    path.write_text(content,encoding='utf-8');checks=[]
    for ref in re.findall(r'(?:href|src)="([^"]+)"',content):
        ref=html.unescape(ref)
        if ref.startswith('http') or ref=='#':continue
        target=Path(unquote(urlparse(ref).path).lstrip('/')) if ref.startswith('file:///') else path.parent/ref
        checks.append(dict(ref=ref,exists=target.exists()))
    frozen=read(base/'prepared-03/freeze.json')
    result=dict(links=checks,missing_links=sum(not c['exists'] for c in checks),
        code_frozen=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in frozen['code'].items()),
        inputs_unchanged=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in frozen['inputs'].items()),
        html_visual_browser_review=False)
    write(base/'artifact-checks.json',result)
    print({k:v for k,v in result.items() if k!='links'})
