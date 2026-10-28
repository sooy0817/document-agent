"""Rank fusion and conservative source-path comparison; no reference answers."""
import re
from .projection import normalize


def rrf(*rankings, k=60):
    found = {}
    for list_index, ranking in enumerate(rankings):
        seen = set()
        for rank, hit in enumerate(ranking, 1):
            key = hit['table_id']
            if key in seen:
                continue
            seen.add(key)
            entry = found.setdefault(key, {**hit, 'score': 0, 'rank_sources': []})
            entry['score'] += 1/(k+rank)
            entry['rank_sources'].append(dict(kind=list_index, rank=rank, score=hit['score']))
    return sorted(found.values(), key=lambda h: (-h['score'], h['table_id']))


def column_matches(column_path, conditions):
    text = ' '.join(column_path)
    years = set(re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', text))
    if conditions['measure'] == '증감':
        return bool(re.search(r'증감|B\s*-\s*A', text, re.I))
    if conditions['periods'] and not set(conditions['periods']).issubset(years):
        return False
    if conditions['measure'] == '구성비':
        return bool(re.search(r'구성비|비중|%', text))
    if conditions['measure'] == '금액':
        return bool(re.search(r'금액|잔액|금\s*액', text)) and not re.search(r'구성비|%', text)
    return True


def row_exactness(path, query):
    """Prefer an explicit leaf/path match, without guessing a synonym or a total."""
    needle = normalize(query)
    labels = [normalize(label) for label in path]
    if needle and labels and needle == labels[-1]:
        return 3
    if needle and labels and needle == ''.join(labels):
        return 4
    if needle and labels and all(t in ''.join(labels) for t in query.split()) and labels[-1] in needle:
        return 2
    return 0
