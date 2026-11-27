from types import SimpleNamespace
from doc_understanding.rag.observability.judge import score_payloads, publish_scores, verify_scores, read_scores


def test_scores_stable_masked_and_bound_to_candidate():
    specs=[dict(name='axis_judge.role',value=2.,data_type='NUMERIC',comment='sk-secretvalue a@example.com')]
    a=score_payloads(specs,'a'*32,'b'*16)
    assert a==score_payloads(specs,'a'*32,'b'*16)
    assert a[0]['observationId']=='b'*16
    assert 'secretvalue' not in a[0]['comment'] and 'example.com' not in a[0]['comment']
    assert verify_scores(a,a)['verified']
    assert not verify_scores(a,[{**a[0],'value':0}])['verified']
    assert not verify_scores(a,[{**a[0],'observationId':'other'}])['verified']


def test_categorical_readback_checks_actual_string():
    a=score_payloads([dict(name='axis_judge.verdict',value='incorrect',data_type='CATEGORICAL',comment='근거')],'a'*32,'b'*16)
    assert verify_scores(a,[{**a[0],'value':0,'stringValue':'incorrect'}])['verified']
    assert not verify_scores(a,[{**a[0],'stringValue':'correct'}])['verified']


def test_telemetry_failure_is_local_and_does_not_raise():
    def fail(**kwargs): raise TimeoutError('not emitted')
    trace=SimpleNamespace(client=SimpleNamespace(api=SimpleNamespace(scores=SimpleNamespace(create=fail))))
    result=publish_scores(trace,{},[dict(name='test',value=0.,data_type='NUMERIC',comment='test')],'a'*32,'b'*16)
    assert result['status']=='failed' and result['error']=='TimeoutError'
    assert publish_scores(SimpleNamespace(client=None),{},[],'a'*32,None)['status']=='unavailable'


def test_v3_compatibility_only_after_404(monkeypatch):
    class NotFound(Exception): status_code=404
    def missing(**kwargs): raise NotFound()
    seen=[]
    def post(url,**kwargs):
        seen.append((url,kwargs['json']));return SimpleNamespace(raise_for_status=lambda:None)
    monkeypatch.setattr('httpx.post',post)
    trace=SimpleNamespace(client=SimpleNamespace(api=SimpleNamespace(scores=SimpleNamespace(create=missing))))
    result=publish_scores(trace,dict(base_url='http://localhost:3000',public_key='test',secret_key='test'),
        [dict(name='test',value=0.,data_type='NUMERIC',comment='test')],'a'*32,'b'*16)
    assert result['write_api']=='legacy_scores_rest' and len(seen)==1


def test_legacy_object_shape_400_fallback_but_not_arbitrary_400(monkeypatch):
    class BadRequest(Exception):
        status_code=400
        body={'error':[{'code':'invalid_type','path':[],'expected':'object','message':'received array'}]}
    def missing(**kwargs): raise BadRequest()
    seen=[]
    monkeypatch.setattr('httpx.post',lambda *a,**k: (seen.append(k) or SimpleNamespace(raise_for_status=lambda:None)))
    trace=SimpleNamespace(client=SimpleNamespace(api=SimpleNamespace(scores=SimpleNamespace(create=missing))))
    config=dict(base_url='http://localhost:3000',public_key='test',secret_key='test')
    spec=[dict(name='test',value=0.,data_type='NUMERIC',comment='test')]
    assert publish_scores(trace,config,spec,'a'*32,'b'*16)['write_api']=='legacy_scores_rest'
    BadRequest.body={'error':[{'code':'invalid_type','path':['value'],'expected':'number','message':'bad'}]}
    assert publish_scores(trace,config,spec,'a'*32,'b'*16)['status']=='failed'
    assert len(seen)==1


def test_legacy_read_paginates_and_filters_other_traces(monkeypatch):
    seen=[]
    def get(*args,**kwargs):
        page=kwargs['params']['page'];seen.append(page)
        return SimpleNamespace(raise_for_status=lambda:None,json=lambda:dict(
            data=[dict(id=str(page),traceId='wanted' if page==2 else 'other')],meta=dict(totalPages=2)))
    monkeypatch.setattr('httpx.get',get)
    result=read_scores(dict(base_url='http://localhost:3000',public_key='test',secret_key='test'),'wanted')
    assert seen==[1,2] and result==[{'id':'2','traceId':'wanted'}]
