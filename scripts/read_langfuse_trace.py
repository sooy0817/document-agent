"""Read back actual v2 observations. Never output credentials."""
import argparse
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src')]
from doc_understanding.runtime.telemetry import Trace, load_config  # noqa: E402
from doc_understanding.rag.observability.trace_reader import read_observations  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument('--config', type=Path, required=True)
p.add_argument('--trace-id', required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
config = load_config(a.config)
trace = Trace(config)
data = read_observations(trace.client, config, a.trace_id)
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')
print(json.dumps(dict(observations=len(data['data']), read_api=data['read_api'],
                     names=[o['name'] for o in data['data']],
                     roots=sum(o.get('parentObservationId') is None for o in data['data']),
                     trace_url=trace.client.get_trace_url(trace_id=a.trace_id))))
