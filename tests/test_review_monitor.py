from doc_understanding.runtime import review_queue
from doc_understanding.web import app
from fastapi.testclient import TestClient


def test_monitor_serves_local_assets_and_does_not_start_models(tmp_path,monkeypatch):
    queue=review_queue.ReviewQueue(tmp_path)
    monkeypatch.setattr(app,'REVIEW_QUEUE',queue)
    with TestClient(app.app) as client:
        response=client.get('/monitor')
        assert response.status_code==200
        assert 'GPT-4.1 mini' in response.text
        assert 'Claude 연결 불필요' in response.text
        assert client.get('/repair_metrics.js').status_code==200
        assert client.get('/api/parser-repairs').json()['config']['enabled'] is False
        assert '선택한 의견의 수정 결과' in response.text
        assert '__TOKEN__' not in response.text
        assert 'id="document-frame"' in response.text
        assert 'id="menu-attention"' in response.text
        assert client.get('/explorer_embed.css').status_code==200
        for path,kind in [('/monitor.js','javascript'),('/monitor_model.js','javascript'),('/monitor.css','text/css')]:
            r=client.get(path)
            assert r.status_code==200 and kind in r.headers['content-type']
        assert not queue.overview()['items']
