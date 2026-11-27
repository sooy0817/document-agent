import hashlib
import json

from doc_understanding.rag.batch import batch


def test_failure_isolation_and_verified_resume(tmp_path_factory):
    # 역할: 실제 worker의 실패 격리·저장·재개를 검사한다. Windows 경로 한도를 위해 짧은 임시 이름을 쓴다.
    tmp_path = tmp_path_factory.mktemp('batch')
    sources=[]
    for name,text in [('bad.html','<table><tr><td><table></table></td></tr></table>'),
                      ('good.html','<h1>News</h1><p>Context</p><table><tr><th>Item</th><th>Value</th></tr><tr><td>A</td><td>0</td></tr></table>')]:
        path=tmp_path/name
        path.write_text(text,encoding='utf8')
        sources.append(dict(source=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps(dict(documents=sources)),encoding='utf8')
    result=batch(manifest,tmp_path/'batch',timeout=30)
    assert result['failed']==1 and result['candidate_stored']==1
    replay=batch(manifest,tmp_path/'batch',timeout=30)
    assert replay['failed']==1 and replay['skipped_verified']==1 and replay['candidate_stored']==0
