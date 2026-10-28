"""Strict arithmetic evidence, independent of semantic relationship approval."""
import re
from decimal import Decimal


def parse_amount(text, *, dash_as_zero=False, blank_as_zero=False):
    """Explicit arithmetic policies only; missing source (None) is always unknown."""
    if text is None:
        return None
    s = (text or '').strip()
    if not s and blank_as_zero:
        return Decimal(0)
    if s == '-' and dash_as_zero:
        return Decimal(0)
    if not re.fullmatch(r'-?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?', s):
        return None
    return Decimal(s.replace(',', ''))


def compare_sum(member_texts, total_text, *, dash_as_zero=False, blank_as_zero=False):
    values = [parse_amount(t, dash_as_zero=dash_as_zero, blank_as_zero=blank_as_zero)
              for t in member_texts]
    total = parse_amount(total_text, dash_as_zero=dash_as_zero, blank_as_zero=blank_as_zero)
    if not values or any(v is None for v in values) or total is None:
        return {'status': 'unresolved', 'sum': None, 'difference': None,
                'reason': '빈칸·대시·비율 또는 비표준 숫자 표기는 확정 계산하지 않음'}
    calculated = sum(values, Decimal(0))
    return {'status': 'match' if calculated == total else 'mismatch',
            'sum': str(calculated), 'difference': str(calculated-total),
            'reason': 'Exact decimal comparison; no rounding tolerance applied'}
