"""Actual saved IR, shared retriever, mocked model. External API never used here."""
import json
import pytest

pytest.importorskip('langgraph')
from doc_understanding.rag import service
from doc_understanding.runtime.telemetry import Trace, redact
from tests.test_document_rag_e2e import QUESTION


def test_trace_failure_does_not_repeat_application_or_raise(tmp_path):
    trace = Trace()
    class Broken:
        def start_as_current_observation(self, **kwargs):
            raise OSError('telemetry down')
    trace.client = Broken()
    calls = []
    with trace.request(QUESTION, 'identity'):
        with trace.span('postgres.search'):
            calls.append(1)
    assert calls == [1] and trace.warnings


def test_privacy_allowlist_and_bounds():
    secret = 'sk-not-a-real-credential'
    safe = redact(dict(question=f'연락 test@example.com {secret}', document_ir={'all': 'PDF'},
                       headers={'authorization': secret}, evidence=[dict(value_raw='963')],
                       notes=[dict(text_raw='x'*10000)], payload={'text': 'whole PDF'}))
    encoded = json.dumps(safe)
    assert secret not in encoded and 'example.com' not in encoded and 'PDF' not in encoded
    assert safe['evidence'][0]['value_raw'] == '963'
    assert len(safe['notes'][0]['text_raw']) == 1600


def test_preflight_is_structural_and_does_not_claim_period_coverage():
    evidence = [dict(evidence_id='E1',column_path=['2025년(B)', '금액'], value_raw='963', lookup_eligible=True)]
    result = service.validate_evidence(evidence, QUESTION.replace('2025', '2026'))
    assert result['sufficient'] and result['question_coverage']=='not_assessed'
    assert result['semantic_status']=='unvalidated_candidate' and result['facts_published']==0


def test_body_period_is_not_forced_to_have_table_columns():
    evidence = [dict(evidence_id='E1', value_raw='2025년 사업 운영 설명', table_id=None, column_path=[])]
    assert service.validate_evidence(evidence, '2025년 사업 운영을 설명해주세요')['sufficient']


def test_exporter_removes_sdk_scope_keys_and_events_without_network():
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.util.instrumentation import InstrumentationScope
    from doc_understanding.runtime.telemetry import scope_safe_exporter
    exporter = scope_safe_exporter(dict(base_url='http://127.0.0.1:3999',
                                       public_key='pk-test-project', secret_key='sk-test-secret'))
    original = ReadableSpan(name='test', instrumentation_scope=InstrumentationScope(
        'langfuse', '5', attributes={'public_key': 'pk-test-project'}), attributes={'safe': 'value'})
    safe = exporter.project(original)
    assert not safe.instrumentation_scope.attributes
    assert safe.attributes == original.attributes
    assert safe.events == () and safe.links == ()
    exporter.shutdown()


def test_explicit_env_config_supports_user_project_and_rejects_missing_keys(tmp_path):
    from doc_understanding.runtime.telemetry import load_config
    path = tmp_path/'client.env.local'
    path.write_text('LANGFUSE_BASE_URL=https://example.invalid\nLANGFUSE_PUBLIC_KEY=pk-test\nLANGFUSE_SECRET_KEY=sk-test\n')
    config = load_config(path)
    assert config['allow_remote'] and config['base_url'] == 'https://example.invalid'
    path.write_text('LANGFUSE_BASE_URL=\n')
    with pytest.raises(ValueError, match='CONFIG_INCOMPLETE'):
        load_config(path)


def test_legacy_trace_readback_fallback_is_read_only_and_mocked(monkeypatch):
    from types import SimpleNamespace
    from doc_understanding.rag.observability.trace_reader import read_observations
    class NotFound(Exception):
        status_code = 404
    def unavailable(**kwargs):
        raise NotFound()
    seen = []
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return dict(id='a'*32, name='test', observations=[dict(name='nh-table-rag')])
    def get(url, **kwargs):
        seen.append(url)
        assert kwargs['auth'] == ('pk-test', 'sk-test')
        return Response()
    import httpx
    monkeypatch.setattr(httpx, 'get', get)
    client = SimpleNamespace(api=SimpleNamespace(observations=SimpleNamespace(get_many=unavailable)))
    result = read_observations(client, dict(base_url='http://localhost:3000', public_key='pk-test',
                                           secret_key='sk-test'), 'a'*32)
    assert result['read_api'] == 'legacy_trace_rest' and len(result['data']) == 1
    assert seen == ['http://localhost:3000/api/public/traces/'+'a'*32]
