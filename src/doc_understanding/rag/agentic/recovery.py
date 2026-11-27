"""Bound recovery to a stated evidence gap and genuinely new source material."""
import json
import re


def norm(text):
    return re.sub(r'\s+','',text or '').casefold()


def request(question, plan, result, tried):
    check=result.get('assessment_validation') or {}
    assessment=check.get('assessment') or {}
    if assessment.get('requires_external_verification'):
        return dict(queries=[],local_gap=False,reason='EXTERNAL_VERIFICATION_REQUIRED')
    errors=set(check.get('reasons') or [])
    if errors-{'QUESTION_REQUIREMENTS_NOT_SUPPORTED','ASSESSMENT_OUTSIDE_SELECTED_EVIDENCE'}:
        return dict(queries=[],local_gap=False,reason='VALIDATION_NOT_RETRIEVAL_FAILURE')
    gaps=[r for r in assessment.get('requirements') or []
          if r.get('status')=='unsupported' and r.get('question_quote') and r['question_quote'] in question]
    queries=[' '.join(dict.fromkeys([*plan.subjects,r['question_quote'],r.get('requirement') or r['question_quote']]))
             for r in gaps]
    missing=(result.get('subject_check') or {}).get('missing') or []
    queries += [s+' '+question for s in missing if s in question]
    seen={norm(q) for q in tried}
    new=[]
    for q in queries:
        if norm(q) not in seen:
            seen.add(norm(q));new.append(q)
    return dict(queries=new[:2],local_gap=bool(gaps or missing),
                reason='MISSING_EVIDENCE' if new else 'NO_NEW_QUERY' if queries else 'NO_RETRIEVABLE_GAP')


def identity(e):
    return (e.get('run_id'),e.get('document_id'),e.get('table_id'),
            e.get('cell_id') or e.get('content_id') or e.get('chunk_id'),e.get('value_raw'))


def atoms(evidence):
    """Ignore ranks/E-numbers; added context counts, removing context does not."""
    result=set()
    for e in evidence:
        key=identity(e)
        result.add(key)
        for field in ('scope_context','unit_context','notes','group_values','nested_context'):
            value=e.get(field)
            if value:
                for item in value if isinstance(value,list) else [value]:
                    result.add((*key,field,json.dumps(item,sort_keys=True,ensure_ascii=False)))
    return result


def merge(previous, found):
    out,seen=list(previous),{identity(e) for e in previous}
    for e in found:
        if identity(e) not in seen:
            out.append(e);seen.add(identity(e))
    return out
