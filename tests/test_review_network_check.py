import socket
from urllib.error import HTTPError, URLError

import pytest

from doc_understanding.runtime import network_check


def dns_ok(*args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('192.0.2.1', 443))]


def test_dns_failure_never_sends_http():
    def failed(*args, **kwargs):
        raise socket.gaierror(11001, 'not found')

    def forbidden(*args, **kwargs):
        pytest.fail('HTTPS must not run after failed DNS')

    result = network_check.inspect(resolver=failed, opener=forbidden)
    assert result['error_code'] == 'DNS_RESOLUTION_FAILED'
    assert result['errno'] == 11001 and result['model_calls'] == 0
    assert not result['ready']


def test_permission_error_is_not_misreported_as_dns():
    def denied(*args, **kwargs):
        raise URLError(OSError(10013, 'access denied'))

    result = network_check.inspect(resolver=dns_ok, opener=denied)
    assert result['dns'] == 'ok'
    assert result['error_code'] == 'NETWORK_ACCESS_DENIED'


@pytest.mark.parametrize('status,ready', [(401, True), (405, True), (403, False), (429, False), (503, False)])
def test_credential_free_probe_checks_reachability_not_model_auth(status, ready):
    def respond(req, timeout):
        assert not req.has_header('Authorization')
        assert req.get_method() == 'HEAD' and req.data is None
        raise HTTPError(req.full_url, status, 'test', {}, None)

    result = network_check.inspect(resolver=dns_ok, opener=respond)
    assert result['ready'] == ready
    assert not result['credentials_checked']


def test_service_does_not_reserve_budget_or_read_key_after_failed_preflight(monkeypatch, tmp_path):
    from doc_understanding.agents.workflows.table_review import service

    monkeypatch.setattr(service.settings, 'configured', lambda data: {'enabled': True, 'daily_limit': 40})
    monkeypatch.setattr(network_check, 'inspect', lambda: {'ready': False, 'error_code': 'DNS_RESOLUTION_FAILED'})
    monkeypatch.setattr(service.credentials, 'model_key', lambda: pytest.fail('No key read when offline'))
    monkeypatch.setattr(service.profiles, 'model_calls', lambda *a, **kw: pytest.fail('No budget adapter when offline'))

    def invoke(record, data, progress, rid, *, calls, trace):
        return calls.call('infer_relations', {}, [], tmp_path)

    monkeypatch.setattr(service, 'run', invoke)
    result = service.execute({}, tmp_path, lambda stage: None, 'test')
    assert result['status'] == 'blocked' and result['api_calls'] == 0
    assert not (tmp_path / 'review-queue/agent-budget.sqlite3').exists()
    assert (tmp_path / 'network-preflight.json').exists()
