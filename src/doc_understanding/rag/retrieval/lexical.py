# 역할: 한글 부분 문자열 검색어와 기존 키워드 점수를 계산한다. 임베딩은 생성하지 않는다.
import re
from functools import lru_cache


def terms(text):
    """Transparent Korean bigram/substring fallback, not a morphological analyzer."""
    tokens = re.findall(r'[가-힣]+|[A-Za-z]+|\d+', text.lower())
    result = set(tokens)-{'the','a','an','of','in','is','what','for','to','and','from','are','be','by','as'}
    for token in tokens:
        if re.fullmatch(r'[가-힣]{3,}', token):
            result.update(token[i:i+2] for i in range(len(token)-1))
    return sorted(result)[:80]



@lru_cache(maxsize=256)
def term_forms(query):
    """원문에 음절 간 공백이 있는 한글 표제도 같은 검색어로 취급한다."""
    return tuple((term, ' '.join(term) if re.fullmatch(r'[가-힣]{2,}', term) else None)
                 for term in terms(query))


def lexical_score(text, query):
    text = text.lower()
    forms = term_forms(query)
    return sum(min(len(term), 6) for term, spaced in forms
               if term in text or (spaced and spaced in text)) / max(1, len(forms))
