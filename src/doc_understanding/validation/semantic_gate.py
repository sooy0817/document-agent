"""Deterministic LLM routing, not semantic truth certification."""

from typing import Literal

from doc_understanding.domain.models import EvidenceModel


class Route(EvidenceModel):
    cell_id: str
    route: Literal['rule', 'llm', 'blocked']
    reasons: tuple[str, ...]
    classify_role: bool = False


def decide(candidate, sources, *, human_conflict=False):
    cid = candidate.cell_id
    if cid not in sources:
        return Route(cell_id=cid, route='blocked', reasons=('SOURCE_MISSING',))
    if 'DEFERRED_FRAGMENT' in candidate.reasons:
        return Route(cell_id=cid, route='blocked', reasons=('DEFERRED_FRAGMENT',))
    refs = candidate.row_ids + candidate.column_ids
    if any(s not in sources for s in refs):
        return Route(cell_id=cid, route='blocked', reasons=('PATH_SOURCE_MISSING',))
    reasons = list(candidate.reasons)
    if not candidate.row_ids or not candidate.column_ids:
        reasons.append('AXIS_PATH_MISSING')
    if not sources[cid].strip() or any(not sources[s].strip() for s in refs):
        reasons.append('SOURCE_TEXT_UNAVAILABLE')
    if cid in refs or len(set(refs)) != len(refs):
        reasons.append('PATH_ID_CONFLICT')
    if max(len(candidate.row_ids), len(candidate.column_ids)) > 8:
        return Route(cell_id=cid, route='blocked', reasons=('PATH_SCHEMA_LIMIT',))
    if candidate.role_uncertain:
        reasons.append('ROLE_AMBIGUOUS')
    if human_conflict:
        reasons.append('HUMAN_CLAIM_CONFLICT')
    return Route(cell_id=cid, route='llm' if reasons else 'rule',
                 reasons=tuple(sorted(set(reasons))) or ('SOURCE_RULES_SUFFICIENT',),
                 classify_role=candidate.role_uncertain)
