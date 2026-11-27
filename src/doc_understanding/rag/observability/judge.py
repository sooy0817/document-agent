# 역할: 평가 점수를 추적 서버에 전송·조회·대조한다. 모델 평가를 재실행하지 않는다.
"""Score transport with v3 server compatibility; never reruns paid inference."""
import uuid
from doc_understanding.runtime.telemetry import redact


def score_payloads(specs, trace_id, observation_id):
    return [dict(id=str(uuid.uuid5(uuid.NAMESPACE_URL,trace_id+'/'+s['name'])),
        traceId=trace_id,observationId=observation_id,name=s['name'],value=s['value'],
        dataType=s['data_type'],comment=redact(s['comment'])) for s in specs]


def publish_scores(trace, config, specs, trace_id, observation_id):
    if not trace.client or not trace_id or not observation_id:
        return dict(status='unavailable',scores=[],error='LANGFUSE_CONTEXT_UNAVAILABLE')
    payloads=score_payloads(specs,trace_id,observation_id)
    records=[]
    # Synchronous public API makes transport failure observable. SDK5 uses v2 batch;
    # existing local v3 write-mode server exposes the legacy public score endpoint.
    try:
        response=trace.client.api.scores.create(request=payloads,request_options={'max_retries':0})
        if getattr(response,'rejected',0):
            return dict(status='partial_failure',write_api='scores_v2',scores=payloads,error='BATCH_ITEMS_REJECTED')
        return dict(status='sent_not_verified',write_api='scores_v2',scores=payloads)
    except Exception as exc:
        body=getattr(exc,'body',{})
        # Old server uses the same path but expects a single score object.
        # Do not reinterpret arbitrary 400 validation failures as compatibility.
        errors=body.get('error',[]) if isinstance(body,dict) else []
        legacy_shape=(getattr(exc,'status_code',None)==400 and isinstance(errors,list) and
            any(isinstance(e,dict) and e.get('code')=='invalid_type' and e.get('path')==[]
                and e.get('expected')=='object' and 'array' in e.get('message','') for e in errors))
        if getattr(exc,'status_code',None)!=404 and not legacy_shape:
            return dict(status='failed',scores=payloads,error=type(exc).__name__)
    import httpx
    for payload in payloads:
        try:
            response=httpx.post(config['base_url'].rstrip('/')+'/api/public/scores',json=payload,
                auth=(config['public_key'],config['secret_key']),timeout=5)
            response.raise_for_status()
            records.append(dict(id=payload['id'],status='sent_not_verified'))
        except Exception as exc:
            records.append(dict(id=payload['id'],status='failed',error=type(exc).__name__))
    return dict(status='sent_not_verified' if all(r['status']=='sent_not_verified' for r in records) else 'partial_failure',
                write_api='legacy_scores_rest',scores=payloads,records=records)


def read_scores(config, trace_id):
    import httpx
    selected=[]
    # Legacy servers can ignore traceId filtering. Filter locally and follow pages;
    # otherwise a score falling outside the first page looks like failed ingestion.
    for page in range(1,101):
        response=httpx.get(config['base_url'].rstrip('/')+'/api/public/scores',
            params={'traceId':trace_id,'limit':100,'page':page},
            auth=(config['public_key'],config['secret_key']),timeout=5)
        response.raise_for_status();body=response.json()
        selected.extend(s for s in body['data'] if s.get('traceId')==trace_id)
        pages=body.get('meta',{}).get('totalPages')
        if (pages is not None and page>=pages) or (pages is None and len(body['data'])<100):
            return selected
    raise ValueError('SCORE_READ_PAGE_BUDGET_EXCEEDED')


def verify_scores(expected, observed):
    by_id={s['id']:s for s in observed}; matches=[]
    for e in expected:
        a=by_id.get(e['id'],{})
        value=a.get('stringValue') if e['dataType']=='CATEGORICAL' else a.get('value')
        matches.append(dict(id=e['id'],name=e['name'],matched=bool(a) and value==e['value']
            and a.get('comment')==e['comment'] and a.get('observationId')==e['observationId']
            and a.get('traceId')==e['traceId'] and a.get('name')==e['name']))
    return dict(verified=bool(expected) and all(m['matched'] for m in matches),scores=matches)
