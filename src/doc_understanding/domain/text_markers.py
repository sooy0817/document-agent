# 역할: 원문 글자에서 번호 표지(1., (2), 가. …)와 문구 역할 후보를 읽는다. 파서와 에이전트가 함께 쓴다.
"""Number markers and surface roles read from the original text only; marker families have no fixed depth."""
import re
import unicodedata

from doc_understanding.domain.models import EvidenceModel


class NumberMarker(EvidenceModel):
    family: str
    token: str
    ordinal: int | None = None


def compact(text):
    return "".join(text.split())


def number_marker(text):
    text = unicodedata.normalize("NFKC", text).lstrip()
    if re.match(r"^\d{4}[.\-/]\d", text):
        return None
    patterns = [
        ("compound", r"^(\d+(?:[-.]\d+)+)[.)]?\s*"),
        ("parenthesized_number", r"^\((\d+)\)\s*"),
        ("roman", r"^([IVXLCDM]+)[.)]\s*"),
        ("decimal", r"^(\d+)[.)\s]\s*"),
        ("letter", r"^([A-Za-z])[.)]\s*"),
        ("hangul", r"^([가-힣])[.)]\s*"),
    ]
    for family, pattern in patterns:
        m = re.match(pattern, text)
        if m:
            return NumberMarker(
                family=family,
                token=m.group(1),
                ordinal=int(m.group(1)) if m.group(1).isdigit() else None,
            )
    return None


def roles(text):
    stripped = text.strip()
    normalized = compact(stripped).lower()
    result = []
    if re.match(r"^\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}", stripped):
        result.append("date_anchor")
    if re.match(r"^[-•●▪]\s+", stripped):
        result.append("bullet")
    if re.match(r"^\(\s*(?:or\b|또\s*는)", stripped, re.I):
        result.append("alternative_label")
    elif stripped.startswith("(") and not number_marker(stripped):
        result.append("related_adjustment")
    if re.search(r"(?:total|subtotal|합계|소계|총계)", normalized):
        result.append("total_candidate")
    return tuple(result)
