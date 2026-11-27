"""Optional, fail-open local tracing. One request root; callback owns graph nodes.

Only bounded projections are exported. CanonicalDocument never enters graph state.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import re

ACTIVE = ContextVar('rag_telemetry', default=None)
BLOCKED = {'document', 'document_ir', 'physical_tables', 'table_structure', 'payload',
           'text', 'primary_text', 'search_text', 'request', 'response', 'headers',
           'authorization', 'api_key', 'secret_key', 'password', 'source_uri'}
SAFE = set('original_question question search_request query run_id limit candidates evidence validation answer result errors retry_count retry_history understanding intent institution periods metrics question_type search_conditions rewritten_query provenance verified_conditions mode sufficient reasons semantic_status lookup_eligible evidence_id document_id table_id cell_id row_path column_path row_node_ids column_node_ids value_raw value_kind unit_context notes table_context text_raw scope status page source_name source_media_type source_refs content_id block_id physical_table_id entry_id kind score rank score_method terms selection evidence_ids values_raw abstained model usage input_tokens output_tokens total_tokens estimated_cost_usd facts_published error node reason query_history original_query fallback generation_mode trace_id query_terms selected attempt phase'.split())
SAFE.update('method conditions measure row_query row_candidates institution_ambiguous institutions run_ids retrieval_relation rank_sources'.split())
SAFE.update('target_id relation mapping_state physical_state context_content_ids span_refs source_cell_ids'.split())
SAFE.update('nested_context parent_cell_id parent_table_id child_table_ids containment_status coverage_status reading_order_status items ref_id prose linkage_algorithm projection_version chunk_id char_start char_end'.split())
SAFE.update('sentences sentence_id sentence_text citations quote source_id sources condition_assessments applicability sentence_validation passed semantic_entailment_verified condition_candidate carrier_evidence_id'.split())
# 질문별 판단도 기존 마스킹·길이 제한을 거쳐 추적한다. 원본 문서 전체는 내보내지 않는다.
SAFE.update('assessment_validation assessment requirements requirement question_quote supports field source_ids requires_external_verification explanation question_coverage model_calls'.split())
# 에이전트·임베딩 호출 기록(ADR-078/079). 입력 원문 대신 개수·해시·요약만 남긴다.
SAFE.update('name call stage provider inputs input_hashes dimensions profile_id elapsed_seconds request_sha256 reserved_cost_usd plan plan_check queries role matched_terms match_score scope_matched scope_context retried retry_quotes structured tables rows columns eligible values choice_id agree aspect decision'.split())


def load_config(path):
    """Only explicitly selected config can authorize an HTTPS remote project."""
    from pathlib import Path
    path = Path(path)
    raw = path.read_text(encoding='utf-8-sig')
    if path.suffix == '.json':
        config = json.loads(raw)
    else:
        values = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' not in line:
                raise ValueError('INVALID_LANGFUSE_CONFIG_LINE')
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip().strip('\"\'')
        config = dict(base_url=values.get('LANGFUSE_BASE_URL', '').rstrip('/'),
                      public_key=values.get('LANGFUSE_PUBLIC_KEY', ''),
                      secret_key=values.get('LANGFUSE_SECRET_KEY', ''))
        # User explicitly chooses this configuration file, never an automatic cloud fallback.
        config['allow_remote'] = config['base_url'].startswith('https://')
    if not all(config.get(k) for k in ('base_url', 'public_key', 'secret_key')):
        raise ValueError('LANGFUSE_CONFIG_INCOMPLETE')
    return config


def scope_safe_exporter(config):
    """SDK v5's mask hook cannot mask scope attributes. Strip public key there too.

    Use the SDK's supported custom exporter argument and public OTEL interfaces;
    HTTP authentication stays in local transport headers, outside trace content.
    """
    import base64
    from importlib.metadata import version
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.trace.export import SpanExporter
    from opentelemetry.sdk.util.instrumentation import InstrumentationScope

    auth = base64.b64encode((config['public_key']+':'+config['secret_key']).encode()).decode()
    delegate = OTLPSpanExporter(endpoint=config['base_url'].rstrip('/')+'/api/public/otel/v1/traces',
        headers={'Authorization': 'Basic '+auth, 'x-langfuse-public-key': config['public_key'],
                 'x-langfuse-sdk-name': 'python', 'x-langfuse-sdk-version': version('langfuse'),
                 'x-langfuse-ingestion-version': '4'}, timeout=2)

    class ScopeSafeExporter(SpanExporter):
        @staticmethod
        def project(s):
            scope = s.instrumentation_scope
            clean = InstrumentationScope(scope.name, scope.version, schema_url=scope.schema_url,
                                         attributes={}) if scope else None
            return ReadableSpan(name=s.name, context=s.context, parent=s.parent, resource=s.resource,
                attributes=s.attributes, events=(), links=(), kind=s.kind, status=s.status,
                start_time=s.start_time, end_time=s.end_time, instrumentation_scope=clean)

        def export(self, spans):
            return delegate.export([self.project(s) for s in spans])

        def shutdown(self):
            delegate.shutdown()

        def force_flush(self, timeout_millis=30000):
            return delegate.force_flush(timeout_millis)
    return ScopeSafeExporter()


def redact(value, *, depth=0):
    """Allowlist, size bounds and credential/PII masking apply also to callbacks."""
    if depth > 12:
        return '[depth-limited]'
    if isinstance(value, dict):
        if 'payload' in value and 'score' in value and 'run_id' in value:
            value = candidates_summary([value])[0]
        return {str(k): redact(v, depth=depth+1) for k, v in value.items()
                if str(k) in SAFE and str(k).lower() not in BLOCKED}
    if isinstance(value, (list, tuple)):
        return [redact(v, depth=depth+1) for v in value[:40]]
    if isinstance(value, str):
        value = re.sub(r'(?i)\b(?:sk|pk|lf-sk|lf-pk)-[\w-]+', '[credential]', value)
        value = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[email]', value)
        value = re.sub(r'\b01[016789][- ]?\d{3,4}[- ]?\d{4}\b', '[phone]', value)
        value = re.sub(r'(?<!\d)\d{6}[- ]?[1-8]\d{6}(?!\d)', '[personal-id]', value)
        return value[:1600]
    return value if isinstance(value, (bool, int, float, type(None))) else '[omitted]'


def mask_otel_spans(*, params):
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch
    patches = {}
    for identifier, span in params.spans.items():
        updates = {}
        deletes = []
        for key, value in span.attributes.items():
            if key.endswith(('.input', '.output', '.metadata')):
                try:
                    parsed = json.loads(value) if isinstance(value, str) else value
                    updates[key] = json.dumps(redact(parsed), ensure_ascii=False)
                except (TypeError, ValueError):
                    updates[key] = '[omitted]'
            elif any(word in key.lower() for word in ('authorization', 'secret', 'password', 'exception', 'http.')):
                deletes.append(key)
        patches[identifier] = OtelSpanPatch(set_attributes=updates, delete_attributes=tuple(deletes))
    return MaskOtelSpansResult(span_patches=patches)


class Trace:
    def __init__(self, config=None):
        self.client = None
        self.public_key = None
        self.trace_id = None
        self.warnings = []
        if config:
            try:
                from langfuse import Langfuse
                # Enforce loopback: accidental cloud forwarding is not an allowed fallback.
                from urllib.parse import urlparse
                url = urlparse(config['base_url'])
                local = url.hostname in ('localhost', '127.0.0.1', '::1')
                if (url.username or url.password or url.query or url.fragment
                        or (not local and not (config.get('allow_remote') and url.scheme == 'https'))):
                    raise ValueError('LANGFUSE_DESTINATION_NOT_EXPLICITLY_ALLOWED')
                self.client = Langfuse(public_key=config['public_key'], secret_key=config['secret_key'],
                                       base_url=config['base_url'], timeout=2, flush_interval=1,
                                       span_exporter=scope_safe_exporter(config),
                                       mask_otel_spans=mask_otel_spans,
                                       environment='local-development')
                self.public_key = config['public_key']
            except Exception:
                self.warnings.append('LANGFUSE_INITIALIZATION_FAILED')

    @contextmanager
    def span(self, name, input=None, *, generation=False):
        cm = observation = None
        if self.client:
            try:
                cm = self.client.start_as_current_observation(name=name,
                    as_type='generation' if generation else 'span', input=redact(input))
                observation = cm.__enter__()
            except Exception:
                cm = None
                self.warnings.append('LANGFUSE_SPAN_FAILED')
        box = {}
        try:
            yield box
        finally:
            if cm:
                try:
                    kwargs = {'output': redact(box.get('output'))}
                    if generation and box.get('output'):
                        result = box['output']
                        usage = result.get('usage', {})
                        kwargs.update(model=result.get('model'), usage_details={
                            'input': usage.get('input_tokens', 0), 'output': usage.get('output_tokens', 0)},
                            cost_details={'total': result.get('estimated_cost_usd', 0)})
                    observation.update(**kwargs)
                except Exception:
                    self.warnings.append('LANGFUSE_EXPORT_FAILED')
                finally:
                    try:
                        cm.__exit__(None, None, None)
                    except Exception:
                        self.warnings.append('LANGFUSE_END_FAILED')

    @contextmanager
    def request(self, question, mode):
        token = ACTIVE.set(self)
        try:
            with self.span('nh-table-rag', {'question': question, 'mode': mode}) as box:
                if self.client:
                    try:
                        self.trace_id = self.client.get_current_trace_id()
                    except Exception:
                        self.warnings.append('LANGFUSE_CONTEXT_FAILED')
                yield box
        finally:
            ACTIVE.reset(token)

    def callbacks(self):
        if not self.client:
            return []
        try:
            from langfuse.langchain import CallbackHandler
            return [CallbackHandler(public_key=self.public_key)]
        except Exception:
            self.warnings.append('LANGFUSE_CALLBACK_FAILED')
            return []

    def flush(self):
        if self.client:
            try:
                self.client.flush()
            except Exception:
                self.warnings.append('LANGFUSE_FLUSH_FAILED')


@contextmanager
def span(name, input=None, **kwargs):
    active = ACTIVE.get()
    if active:
        with active.span(name, input, **kwargs) as box:
            yield box
    else:
        yield {}


def candidates_summary(hits):
    result = []
    for i, hit in enumerate(hits):
        p = hit['payload']
        item = dict(rank=i+1, score=hit['score'], run_id=hit['run_id'],
                    **{k: p.get(k) for k in ('entry_id', 'table_id', 'kind', 'page')})
        # Canonical reference IDs are document_id + /pNNNN/... (no document load).
        ref = re.match(r'^(sha256:[a-f0-9]{64})/p\d+/', p.get('entry_id', ''))
        item['document_id'] = p.get('document_id') or (ref.group(1) if ref else None)
        result.append(item)
    return result
