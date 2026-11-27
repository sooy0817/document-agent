# 역할: 모델 호출 전 근거의 사용 가능 여부만 검사한다. 질문 의미는 판정하지 않는다.
"""Structural preflight, not a question classifier or semantic sufficiency test."""


def lookup_eligible(evidence):
    # 본문은 표 매핑을 요구하지 않는다. 표 셀은 명시적으로 해결된 매핑이 필요하다.
    return (isinstance(evidence.get('value_raw'), str)
            and evidence.get('lookup_eligible') is not False
            and (not evidence.get('table_id') or evidence.get('lookup_eligible') is True))


def validate_evidence(evidence, question):
    eligible = [e for e in evidence if lookup_eligible(e)
                and isinstance(e.get('evidence_id'), str) and e['evidence_id']
                and isinstance(e.get('value_raw'), str)]
    reasons = [] if eligible else ['NO_ELIGIBLE_EVIDENCE']
    ids = [e.get('evidence_id') for e in evidence]
    if any(not isinstance(eid, str) or not eid for eid in ids):
        reasons.append('INVALID_EVIDENCE_ID')
    elif len(set(ids)) != len(ids):
        reasons.append('DUPLICATE_EVIDENCE_ID')
    return dict(sufficient=not reasons, reasons=reasons,
                scope='structural_preflight_only', question_coverage='not_assessed',
                semantic_status='unvalidated_candidate', facts_published=0)
