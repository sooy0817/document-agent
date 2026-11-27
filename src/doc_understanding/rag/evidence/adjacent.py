"""Bounded, read-only recovery of nearby narrative evidence after reranking."""
from doc_understanding.storage.staging.local import query_params


def neighbors(run_id, content_ids):
    rows = query_params("""SELECT DISTINCT n.item_id, n.payload
      FROM rag.evidence_item c JOIN rag.evidence_item n ON n.run_id=c.run_id AND n.kind='content'
      WHERE c.run_id=%s AND c.kind='content' AND c.item_id=ANY(%s)
      AND (n.payload->>'position')::int BETWEEN (c.payload->>'position')::int-2 AND (c.payload->>'position')::int+2
      AND n.payload->'source'->>'page_number'=c.payload->'source'->>'page_number'
      AND n.payload->'body' IS NOT NULL AND n.payload->'body'<>'null'::jsonb
      AND length(n.payload->>'text_raw') BETWEEN 1 AND 2000
      ORDER BY n.item_id LIMIT 20""", [run_id, list(content_ids)])
    return [r['payload'] for r in rows]


def expand(evidence, loader=neighbors, *, max_carriers=3, max_added=8):
    """Keep separate originals/IDs, never join nearby text into a fabricated passage."""
    out, groups = list(evidence), {}
    bodies = [e for e in evidence if not e.get('table_id') and e.get('content_id') and e.get('run_id')][:max_carriers]
    seen = {(e.get('run_id'),e.get('content_id')) for e in evidence}
    for e in bodies:
        groups.setdefault(e['run_id'],[]).append(e['content_id'])
    for run_id, ids in groups.items():
        candidates=sorted(loader(run_id,ids),key=lambda c:c['position'])
        for c in candidates:
            key=(run_id,c['content_id'])
            if key in seen or not c.get('body') or not c['text_raw'].strip():
                continue
            if len(out)-len(evidence)>=max_added:
                return out
            out.append(dict(c['body'],run_id=run_id,evidence_id=f'E{len(out)+1}',
                            retrieval_context='adjacent_narrative'))
            seen.add(key)
    return out
