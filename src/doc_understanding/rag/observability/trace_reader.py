# 역할: 추적 서버의 기존 실행 기록을 조회하며 서버 설정이나 실행을 변경하지 않는다.
"""Read server records, including the user's existing v3 write-mode server.

This does not change ingestion or server settings. New isolated server uses v2.
"""
import re


def read_observations(client, config, trace_id):
    if not re.fullmatch(r'[0-9a-f]{32}', trace_id):
        raise ValueError('INVALID_TRACE_ID')
    try:
        result = client.api.observations.get_many(trace_id=trace_id,
            fields='core,basic,io,usage,model,metadata', limit=1000)
        return {**result.model_dump(mode='json', by_alias=True), 'read_api': 'observations_v2'}
    except Exception as error:
        if getattr(error, 'status_code', None) != 404:
            raise
        # SDK 5 removed the legacy trace client. Use the existing server's public REST API.
        import httpx
        response = httpx.get(config['base_url'].rstrip('/')+'/api/public/traces/'+trace_id,
                             auth=(config['public_key'], config['secret_key']), timeout=5)
        response.raise_for_status()
        trace = response.json()
        return dict(data=trace.get('observations', []), read_api='legacy_trace_rest',
                    trace_id=trace['id'], trace_name=trace.get('name'))
