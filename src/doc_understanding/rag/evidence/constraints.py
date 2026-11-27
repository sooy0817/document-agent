# 역할: 질문 원문과 호출자가 지정한 출처에 근거를 제한한다. 이름의 의미를 추정하지 않는다.
"""Source constraints supplied by callers or anchored in the original question.

This is lexical evidence checking, not a named-entity recognizer. No institution
suffix, financial dictionary, or model guess is treated as an authoritative name.
"""
import re
import unicodedata


def normalized(text):
    return ' '.join(unicodedata.normalize('NFKC', text or '').casefold().split())


def contains(text, term):
    """Match literal names without accepting ACME as a prefix of ACMEX."""
    text, term = normalized(text), normalized(term)
    if not term:
        return False
    return bool(re.search(r'(?<![^\W_])' + re.escape(term) + r'(?![A-Za-z0-9])', text))


def constrain_sources(evidence, entities):
    """Require coverage of EVERY requested source, and exclude unrelated sources."""
    if not entities:
        return list(evidence), []
    selected = [e for e in evidence if any(contains(e.get('source_name'), n) for n in entities)]
    missing = [n for n in entities if not any(contains(e.get('source_name'), n) for e in selected)]
    return selected, missing
