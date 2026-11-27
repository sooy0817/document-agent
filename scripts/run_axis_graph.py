"""Run the bounded axis graph on an immutable axis-resolution source directory.

Default offline. --replay text|vision reuses saved outputs (not new model evidence).
--live text|vision --allow-external explicitly enables the existing bounded adapter.
No source JSON, CanonicalDocument, PostgreSQL or default extraction path is changed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from doc_understanding.agents.axis_review.axis_graph import build_axis_graph
from doc_understanding.agents.axis_review.axis_resolver import AxisRegion, AxisProposal
from doc_understanding.agents.axis_review.axis_provider import AxisModelAdapter


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)


def source_image(source, name):
    local = source / f'{name}.png'
    if local.exists():
        return local
    # Replayed sidecars retain an explicit original crop reference.
    manifest = read(source / 'manifest.json')
    record = next(r for r in manifest['regions'] if r['source'] == name+'-source.json')
    image = Path(record['source_image'])
    if not image.is_file():
        raise ValueError('SOURCE_IMAGE_UNAVAILABLE')
    return image


def run(source, output, *, replay=None, live=None, allow_external=False, max_calls=4):
    if replay and live:
        raise ValueError('REPLAY_AND_LIVE_ARE_EXCLUSIVE')
    if live and not allow_external:
        raise ValueError('EXTERNAL_CALL_NOT_AUTHORIZED')
    sources = sorted(source.glob('region-*-source.json'))
    if not sources:
        raise ValueError('NO_AXIS_SOURCE_REGIONS')
    # Hash every input file, including saved model results and original baseline.
    hashes = {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in source.iterdir() if p.is_file()}
    baselines = read(source / 'baseline-structured.json')
    output.mkdir(parents=True, exist_ok=False)
    adapter = AxisModelAdapter(allow_external=allow_external, max_calls=max_calls)
    summaries = []
    for path in sources:
        name = path.name.removesuffix('-source.json')
        region = AxisRegion.model_validate(read(path))
        tids = {c.table_id for c in region.cells}
        selected = [t for t in baselines if t['table_id'] in tids]
        resolver = None
        if replay:
            saved = source / f'{name}-{replay}.json'
            if saved.exists():
                response = read(saved)
                def resolver(r, response=response):
                    return AxisProposal.model_validate(response['proposal']), dict(
                        status='replayed', historical_status=response.get('saved_call_status') or
                        (response.get('call') or {}).get('status'),
                        actual_api_calls=0, estimated_cost_usd=0)
        elif live:
            image = source_image(source, name).read_bytes() if live == 'vision' else None
            def resolver(r, image=image):
                return adapter.resolve(r, image)
        graph = build_axis_graph(region, selected, resolver=resolver)
        result = graph.invoke({}, config={'recursion_limit': 12})
        write(output / f'{name}-graph.json', result)
        write(output / f'{name}-structured.json', result['structured'])
        if not (output / 'graph.mmd').exists():
            (output / 'graph.mmd').write_text(graph.get_graph().draw_mermaid(), encoding='utf-8')
        summaries.append(dict(region_id=region.id, source=path.name,
            result=f'{name}-graph.json', route_reason=result['route_reason'],
            model_invocations=result['model_invocations'],
            supported_candidates=sum(c['status'] == 'supported_candidate'
                                     for c in result['validation']['checked_links']),
            rejected=sum(c['status'] == 'unresolved' for c in result['validation']['checked_links']),
            cells=result['physical_cells'], physical_usable=result['physical_usable']))
    unchanged = all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == h for p, h in hashes.items())
    if not unchanged:
        raise RuntimeError('SOURCE_CHANGED_DURING_RUN')
    manifest = dict(schema_version='axis-graph-run/1.0', source=str(source.resolve()),
        mode='live-'+live if live else 'replay-'+replay if replay else 'rules',
        original_hashes=hashes, originals_unchanged=unchanged, regions=summaries,
        actual_api_attempts=adapter.calls, facts_published=0, semantic_verified=False)
    write(output / 'manifest.json', manifest)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--replay', choices=['text', 'vision'])
    modes.add_argument('--live', choices=['text', 'vision'])
    parser.add_argument('--allow-external', action='store_true')
    parser.add_argument('--max-calls', type=int, default=4)
    args = parser.parse_args()
    result = run(**vars(args))
    print(json.dumps({k: v for k, v in result.items() if k != 'original_hashes'}, ensure_ascii=False))
