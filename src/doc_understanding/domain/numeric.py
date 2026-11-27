# 역할: 원문 숫자·공란·부호를 보존하면서 검색과 표 후보에서 쓸 해석 후보를 만든다.
import re
from decimal import Decimal, InvalidOperation


# 원문 문자열을 (종류, 숫자 문자열 후보)로 분류한다. 삼각 부호의 업무 의미는 확정하지 않는다.
def parse_value(raw):
    if raw is None:
        return 'unavailable', None
    value = re.sub(r'\s+', '', raw)
    if value == '':
        return 'blank', None
    if value in ('-', '－', '—'):
        return 'hyphen', None
    if value in ('N/A', '해당없음'):
        return 'not_applicable', None
    triangle = value.startswith(('△', '▲'))
    candidate = '-' + value[1:] if triangle else value
    if re.fullmatch(r'\(\d[\d,.]*\)', candidate):
        candidate = '-' + candidate[1:-1]
    if not re.fullmatch(r'[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?', candidate):
        return 'text', None
    try:
        parsed = str(Decimal(candidate.rstrip('%').replace(',', '')))
    except InvalidOperation:
        return 'text', None
    # A triangle's accounting meaning requires downstream validation.
    return ('triangle_decrease_candidate' if triangle else 'number'), parsed
