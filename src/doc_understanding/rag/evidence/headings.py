"""Carry existing section-heading candidates with narrative evidence at read time."""
from doc_understanding.storage.staging.local import query_params


def load(run_id, content_ids):
    rows = query_params("""SELECT a.payload->'sections' AS sections,
      (SELECT jsonb_agg(c) FROM jsonb_array_elements(a.payload->'contents') c
       WHERE c->>'content_id'=ANY(%s) OR c->>'content_id' IN
         (SELECT s->'content_ids'->>0 FROM jsonb_array_elements(a.payload->'sections') s)) AS contents
      FROM staging.artifact a WHERE a.run_id=%s AND a.kind='CanonicalDocument'""", [list(content_ids),run_id])
    return rows[0] if len(rows)==1 else {}


def contexts(item, structure):
    """Follow stored membership/parents, verifying each title against its original.

    No guessing from nearby text, no reconstructed hierarchy, no scope approval.
    """
    contents={c['content_id']:c for c in structure.get('contents') or []}
    sections={s['section_id']:s for s in structure.get('sections') or []}
    content=contents.get(item.get('content_id'))
    if not content:
        return []
    current=content.get('section_id')
    seen, result = set(), []
    while current and current not in seen and len(seen)<8:
        seen.add(current)
        section=sections.get(current)
        if not section:
            break
        if len(seen)==1 and content['content_id'] not in section['content_ids']:
            return []
        head=contents.get(next(iter(section['content_ids']),None))
        if head and head['content_id']!=content['content_id'] and head.get('text_raw'):
            refs=head.get('source_references') or []
            if (head['text_raw'].strip()==section['title_raw'].strip() and refs
                    and all(r['document_id']==item.get('document_id') for r in refs)):
                result.append(dict(content_id=head['content_id'],text_raw=head['text_raw'],
                                   page=refs[0]['page_number'],source_references=refs,
                                   span_refs=[r['span_id'] for r in refs if r.get('span_id')],
                                   semantic_status='unvalidated_candidate'))
        current=section.get('parent_id')
    return list(reversed(result))


def attach(evidence, loader=load):
    groups={}
    for e in evidence:
        if not e.get('table_id') and e.get('content_id') and e.get('run_id'):
            groups.setdefault(e['run_id'],[]).append(e['content_id'])
    structures={rid:loader(rid,ids) for rid,ids in groups.items()}
    out=[]
    for e in evidence:
        extra=contexts(e,structures[e['run_id']]) if e.get('run_id') in structures and not e.get('table_id') else []
        kept=list(e.get('scope_context') or [])
        seen={c.get('content_id') for c in kept}
        out.append(dict(e,scope_context=kept+[c for c in extra if c['content_id'] not in seen]) if extra else e)
    return out
