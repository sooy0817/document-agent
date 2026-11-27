"""Authenticate an explicitly selected project and create a safe connection trace.

No document, OpenAI call or secret is written into this trace or the output file.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src')]
from doc_understanding.runtime.telemetry import Trace, load_config  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument('--config', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
trace = Trace(load_config(a.config))
if not trace.client or not trace.client.auth_check():
    raise SystemExit('LANGFUSE_AUTHENTICATION_FAILED; check saved settings (no keys printed)')
with trace.request('NH Table RAG connection check', 'connection_check') as record:
    record['output'] = dict(status='connected', facts_published=0)
trace.flush()
result = dict(authenticated=True, trace_id=trace.trace_id,
              trace_url=trace.client.get_trace_url(trace_id=trace.trace_id),
              persistence='read_back_with_read_langfuse_trace.py', model_calls=0, estimated_cost_usd=0)
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
print(json.dumps(result))
