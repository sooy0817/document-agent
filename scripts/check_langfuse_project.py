"""Read the explicitly selected configuration's project, without logging keys."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src')]
from doc_understanding.runtime.telemetry import Trace, load_config  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument('--config', type=Path, required=True)
p.add_argument('--expected-project')
a = p.parse_args()
config = load_config(a.config)
trace = Trace(config)
if not trace.client:
    raise SystemExit('LANGFUSE_INITIALIZATION_FAILED')
projects = trace.client.api.projects.get()
items = [dict(id=project.id, name=project.name) for project in projects.data]
print(json.dumps(dict(config_file=str(a.config.resolve()), base_url=config['base_url'],
                      projects=items, keys_printed=False), ensure_ascii=False))
if a.expected_project and not any(item['name'] == a.expected_project for item in items):
    raise SystemExit('LANGFUSE_PROJECT_MISMATCH')
